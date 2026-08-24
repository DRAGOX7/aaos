"""Private, fixed-route Playwright binding for Moodle collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from aaos.moodle.browser_models import BrowserPolicyError, BrowserRoute
from aaos.moodle.browser_policy import build_browser_url, classify_browser_request

_NAVIGATION_TIMEOUT_MS = 30_000
FailureStage = Literal[
    "COLLECTION_PAGE_CREATE",
    "REQUEST_POLICY",
    "UNEXPECTED_REDIRECT",
    "NAVIGATION",
    "NONE",
]
RedirectClass = Literal[
    "LOGIN_ROUTE",
    "SSO_EXTERNAL",
    "COURSE_INDEX_VARIANT",
    "MOODLE_MY_ROUTE",
    "OTHER_SAME_ORIGIN",
    "OTHER_ORIGIN",
    "INVALID",
]
RequestMethodClass = Literal["GET", "HEAD", "POST", "OTHER"]
RequestOriginClass = Literal["MOODLE_SAME_ORIGIN", "OTHER_ORIGIN"]
RequestRouteClass = Literal[
    "COURSE_INDEX", "STATIC_ASSET", "MOODLE_AJAX_SERVICE", "OTHER_MOODLE_ROUTE", "UNKNOWN"
]
RequestResourceType = Literal[
    "DOCUMENT", "SCRIPT", "STYLESHEET", "XHR_FETCH", "IMAGE", "FONT", "OTHER"
]
RequestPolicyResult = Literal["ALLOWED", "BLOCKED"]
StaticAssetClass = Literal[
    "THEME_STYLES",
    "MOODLE_CORE_JS",
    "MOODLE_AMD_JS",
    "THEME_JS",
    "THEME_IMAGE",
    "CORE_IMAGE",
    "FONT",
    "OTHER_STATIC_SAME_ORIGIN",
    "PLUGINFILE",
    "OTHER_MOODLE",
    "OTHER_ORIGIN",
]


@dataclass(frozen=True, slots=True)
class BrowserDocument:
    route: BrowserRoute
    final_origin: str
    final_path: str
    content_type: str
    html: str


@dataclass(frozen=True, slots=True)
class BrowserRequestObservation:
    """One safe, structural request classification with no URL or payload retention."""

    method: RequestMethodClass
    origin: RequestOriginClass
    route: RequestRouteClass
    resource_type: RequestResourceType
    policy_result: RequestPolicyResult

    def render(self) -> str:
        """Render only fixed safe classification values."""
        return "|".join(
            (self.method, self.origin, self.route, self.resource_type, self.policy_result)
        )


@dataclass(frozen=True, slots=True)
class BrowserRequestSummary:
    """Aggregate safe request evidence; it never retains request content."""

    same_origin_xhr_fetch_count: int = 0
    moodle_ajax_service_count: int = 0
    blocked_post_moodle_ajax_service_count: int = 0
    allowed_static_request_count: int = 0
    static_counts: tuple[tuple[StaticAssetClass, int, int, int], ...] = ()


class _RequestObserver:
    def __init__(self) -> None:
        self._same_origin_xhr_fetch_count = 0
        self._moodle_ajax_service_count = 0
        self._blocked_post_moodle_ajax_service_count = 0
        self._allowed_static_request_count = 0
        self._static_counts: dict[StaticAssetClass, list[int]] = {}

    def record(self, observation: BrowserRequestObservation) -> None:
        if observation.origin == "MOODLE_SAME_ORIGIN" and observation.resource_type == "XHR_FETCH":
            self._same_origin_xhr_fetch_count += 1
        if observation.route == "MOODLE_AJAX_SERVICE":
            self._moodle_ajax_service_count += 1
        if (
            observation.method == "POST"
            and observation.route == "MOODLE_AJAX_SERVICE"
            and observation.policy_result == "BLOCKED"
        ):
            self._blocked_post_moodle_ajax_service_count += 1
        if observation.route == "STATIC_ASSET" and observation.policy_result == "ALLOWED":
            self._allowed_static_request_count += 1

    def record_static(
        self, static_class: StaticAssetClass, policy_result: RequestPolicyResult
    ) -> None:
        counts = self._static_counts.setdefault(static_class, [0, 0, 0])
        counts[0] += 1
        counts[1 if policy_result == "ALLOWED" else 2] += 1

    def snapshot(self) -> BrowserRequestSummary:
        return BrowserRequestSummary(
            same_origin_xhr_fetch_count=self._same_origin_xhr_fetch_count,
            moodle_ajax_service_count=self._moodle_ajax_service_count,
            blocked_post_moodle_ajax_service_count=self._blocked_post_moodle_ajax_service_count,
            allowed_static_request_count=self._allowed_static_request_count,
            static_counts=tuple(
                (key, values[0], values[1], values[2])
                for key, values in sorted(self._static_counts.items())
            ),
        )


class RestrictedBrowserNavigator(Protocol):
    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument: ...

    def close(self) -> None: ...


class _Route(Protocol):
    def abort(self) -> None: ...

    def continue_(self) -> None: ...


class _Request(Protocol):
    method: str
    url: str
    resource_type: str


class _Page(Protocol):
    url: str

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object: ...

    def content(self) -> str: ...

    def wait_for_timeout(self, milliseconds: int) -> None: ...

    def close(self) -> None: ...

    def on(self, event: str, handler: object) -> None: ...


class _Context(Protocol):
    pages: list[_Page]

    def route(self, pattern: str, handler: object) -> None: ...

    def new_page(self) -> _Page: ...

    def close(self) -> None: ...


class PlaywrightRestrictedNavigator:
    """Expose only fixed route navigation, never raw browser primitives."""

    def __init__(
        self,
        context: _Context,
        base_origin: str,
        *,
        canonical_wwwroot: str | None = None,
        transition_sentinel: _Page | None = None,
    ) -> None:
        self._context = context
        self._base_origin = base_origin
        self._canonical_wwwroot = canonical_wwwroot or base_origin + "/current"
        self._transition_sentinel = transition_sentinel
        self._closed = False
        self._failure_stage: FailureStage = "NONE"
        self._redirect_class: RedirectClass | None = None
        self._request_observer = _RequestObserver()
        self._install_interception()

    @property
    def failure_stage(self) -> FailureStage:
        """Return only a fixed non-sensitive failure classification."""
        return self._failure_stage

    @property
    def redirect_class(self) -> RedirectClass | None:
        """Return the safe class of an unexpected final navigation, if any."""
        return self._redirect_class

    @property
    def request_summary(self) -> BrowserRequestSummary:
        """Return fixed aggregate request facts without request data."""
        return self._request_observer.snapshot()

    def _install_interception(self) -> None:
        self._context.route("**/*", self._handle_request)

    @staticmethod
    def _same_pages(actual: object, expected: tuple[_Page, ...]) -> bool:
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(sum(page is candidate for page in actual) == 1 for candidate in expected)
        )

    def _complete_transition(self, collection_page: _Page) -> None:
        sentinel = self._transition_sentinel
        if sentinel is None:
            return
        if (
            not self._same_pages(self._context.pages, (sentinel, collection_page))
            or sentinel.url != "about:blank"
        ):
            raise BrowserPolicyError()
        try:
            sentinel.close()
        except (OSError, PlaywrightError, TypeError, ValueError):
            raise BrowserPolicyError() from None
        if not self._same_pages(self._context.pages, (collection_page,)):
            raise BrowserPolicyError()
        self._transition_sentinel = None

    def _handle_request(self, route: _Route, request: _Request) -> None:
        observation = classify_request_observation(
            request.method,
            request.url,
            request.resource_type,
            base_origin=self._base_origin,
            canonical_wwwroot=self._canonical_wwwroot,
        )
        self._request_observer.record(observation)
        self._request_observer.record_static(
            classify_static_asset_request(request.url, self._canonical_wwwroot),
            observation.policy_result,
        )
        if (
            classify_browser_request(
                request.method,
                request.url,
                base_origin=self._base_origin,
                canonical_wwwroot=self._canonical_wwwroot,
                resource_type=request.resource_type,
            ).code
            == "ALLOW"
        ):
            route.continue_()
        else:
            self._failure_stage = "REQUEST_POLICY"
            route.abort()

    @staticmethod
    def _blocked_event(*_args: object) -> None:
        raise BrowserPolicyError()

    def _close_owned_page(self, page: _Page) -> None:
        """Close a collection page without reclassifying an already-closed target."""
        try:
            page.close()
        except PlaywrightError as error:
            if type(error).__name__ != "TargetClosedError":
                raise BrowserPolicyError() from None
            try:
                pages = self._context.pages
            except PlaywrightError:
                return
            if isinstance(pages, list) and all(candidate is not page for candidate in pages):
                return
            raise BrowserPolicyError() from None
        except (OSError, TypeError, ValueError):
            raise BrowserPolicyError() from None

    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument:
        if self._closed:
            raise BrowserPolicyError()
        target = build_browser_url(route, base_origin=self._base_origin, identifier=identifier)
        try:
            page = self._context.new_page()
        except (OSError, PlaywrightError, ValueError, TypeError):
            self._failure_stage = "COLLECTION_PAGE_CREATE"
            raise BrowserPolicyError() from None
        try:
            for event in ("popup", "download", "websocket", "filechooser"):
                page.on(event, self._blocked_event)
            self._complete_transition(page)
            page.goto(target, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)
            parsed = urlsplit(page.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin != self._base_origin or parsed.path != urlsplit(target).path or parsed.query:
                self._failure_stage = "UNEXPECTED_REDIRECT"
                self._redirect_class = classify_redirect(page.url, self._base_origin)
                raise BrowserPolicyError()
            html = page.content()
            if not isinstance(html, str):
                raise BrowserPolicyError()
            return BrowserDocument(route, origin, parsed.path, "text/html", html)
        except (OSError, TimeoutError, ValueError, TypeError):
            if self._failure_stage == "NONE":
                self._failure_stage = "NAVIGATION"
            raise BrowserPolicyError() from None
        finally:
            self._close_owned_page(page)

    def observe_course_index_loading(self) -> tuple[str, str, str]:
        """Sample only bounded COURSE_INDEX markup while interception remains enforced."""
        if self._closed:
            raise BrowserPolicyError()
        target = build_browser_url("COURSE_INDEX", base_origin=self._base_origin)
        try:
            page = self._context.new_page()
        except (OSError, PlaywrightError, ValueError, TypeError):
            self._failure_stage = "COLLECTION_PAGE_CREATE"
            raise BrowserPolicyError() from None
        try:
            for event in ("popup", "download", "websocket", "filechooser"):
                page.on(event, self._blocked_event)
            self._complete_transition(page)
            page.goto(target, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)
            parsed = urlsplit(page.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin != self._base_origin or parsed.path != urlsplit(target).path or parsed.query:
                self._failure_stage = "UNEXPECTED_REDIRECT"
                self._redirect_class = classify_redirect(page.url, self._base_origin)
                raise BrowserPolicyError()
            initial = page.content()
            page.wait_for_timeout(500)
            intermediate = page.content()
            page.wait_for_timeout(500)
            settled = page.content()
            if not all(isinstance(sample, str) for sample in (initial, intermediate, settled)):
                raise BrowserPolicyError()
            return initial, intermediate, settled
        except (OSError, TimeoutError, ValueError, TypeError):
            if self._failure_stage == "NONE":
                self._failure_stage = "NAVIGATION"
            raise BrowserPolicyError() from None
        finally:
            self._close_owned_page(page)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._context.close()
        except (OSError, ValueError, TypeError):
            raise BrowserPolicyError() from None


def classify_request_observation(
    method: object,
    url: object,
    resource_type: object,
    *,
    base_origin: str,
    canonical_wwwroot: str | None = None,
) -> BrowserRequestObservation:
    """Classify an intercepted request without retaining raw request data."""
    method_class: RequestMethodClass = method if method in {"GET", "HEAD", "POST"} else "OTHER"
    resource_class = cast(
        RequestResourceType,
        {
            "document": "DOCUMENT",
            "script": "SCRIPT",
            "stylesheet": "STYLESHEET",
            "xhr": "XHR_FETCH",
            "fetch": "XHR_FETCH",
            "image": "IMAGE",
            "font": "FONT",
        }.get(resource_type if isinstance(resource_type, str) else "", "OTHER"),
    )
    safe_url = url if isinstance(url, str) else ""
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        base = urlsplit(base_origin)
    except ValueError:
        parsed = None
        base = None
    same_origin = (
        parsed is not None
        and base is not None
        and f"{parsed.scheme}://{parsed.netloc}" == f"{base.scheme}://{base.netloc}"
    )
    origin_class: RequestOriginClass = "MOODLE_SAME_ORIGIN" if same_origin else "OTHER_ORIGIN"
    if not same_origin or parsed is None:
        route_class: RequestRouteClass = "UNKNOWN"
    elif parsed.path == "/current/my/courses.php":
        route_class = "COURSE_INDEX"
    elif parsed.path in {"/current/lib/ajax/service.php", "/current/lib/ajax/service-nologin.php"}:
        route_class = "MOODLE_AJAX_SERVICE"
    elif (
        classify_browser_request(
            "GET",
            safe_url,
            base_origin=base_origin,
            canonical_wwwroot=canonical_wwwroot,
            resource_type=resource_type if isinstance(resource_type, str) else None,
        ).code
        == "ALLOW"
    ):
        route_class = "STATIC_ASSET"
    else:
        route_class = "OTHER_MOODLE_ROUTE"
    policy_result: RequestPolicyResult = (
        "ALLOWED"
        if isinstance(method, str)
        and classify_browser_request(
            method,
            safe_url,
            base_origin=base_origin,
            canonical_wwwroot=canonical_wwwroot,
            resource_type=resource_type if isinstance(resource_type, str) else None,
        ).code
        == "ALLOW"
        else "BLOCKED"
    )
    return BrowserRequestObservation(
        method=method_class,
        origin=origin_class,
        route=route_class,
        resource_type=resource_class,
        policy_result=policy_result,
    )


def classify_static_asset_request(url: object, canonical_wwwroot: str) -> StaticAssetClass:
    """Classify only fixed Moodle infrastructure route families without approving them."""
    if not isinstance(url, str):
        return "OTHER_ORIGIN"
    try:
        target = urlsplit(url)
        base = urlsplit(canonical_wwwroot)
    except ValueError:
        return "OTHER_ORIGIN"
    if f"{target.scheme}://{target.netloc}" != f"{base.scheme}://{base.netloc}":
        return "OTHER_ORIGIN"
    base_path = base.path.rstrip("/")
    if (
        base.scheme != "https"
        or not base.netloc
        or not base_path
        or base.query
        or base.fragment
        or base.username
        or base.password
    ):
        return "OTHER_MOODLE"
    prefix = base_path + "/"
    if not target.path.startswith(prefix):
        return "OTHER_MOODLE"
    path = target.path[len(base_path) :]
    if path == "/theme/styles.php" or path.startswith("/theme/styles.php/"):
        return "THEME_STYLES"
    if path == "/lib/javascript.php" or path.startswith("/lib/javascript.php/"):
        return "MOODLE_CORE_JS"
    if (
        path == "/lib/requirejs.php"
        or path.startswith("/lib/requirejs.php/")
        or path.startswith("/lib/amd/")
        and path.endswith(".js")
    ):
        return "MOODLE_AMD_JS"
    if path.startswith("/theme/") and path.endswith(".js"):
        return "THEME_JS"
    if path.startswith("/theme/") and path.endswith((".png", ".svg", ".jpg", ".webp")):
        return "THEME_IMAGE"
    if path.startswith("/pix/"):
        return "CORE_IMAGE"
    if path.startswith("/theme/") and path.endswith((".woff", ".woff2", ".ttf")):
        return "FONT"
    if path == "/pluginfile.php" or path.startswith("/pluginfile.php/"):
        return "PLUGINFILE"
    if path.endswith((".css", ".js", ".png", ".svg", ".jpg", ".webp")):
        return "OTHER_STATIC_SAME_ORIGIN"
    return "OTHER_MOODLE"


def classify_redirect(url: object, base_origin: str) -> RedirectClass:
    """Classify a final redirect structurally without returning its destination."""
    if not isinstance(url, str):
        return "INVALID"
    try:
        target = urlsplit(url)
        base = urlsplit(base_origin)
    except ValueError:
        return "INVALID"
    target_origin = f"{target.scheme}://{target.netloc}"
    canonical_origin = f"{base.scheme}://{base.netloc}"
    if target.scheme != "https" or not target.netloc:
        return "INVALID"
    if target_origin != canonical_origin:
        if target.hostname == "login.microsoftonline.com":
            return "SSO_EXTERNAL"
        return "OTHER_ORIGIN"
    if target.path.startswith("/current/login/") or target.path.startswith("/current/auth/"):
        return "LOGIN_ROUTE"
    if target.path == "/current/my/courses.php":
        return "COURSE_INDEX_VARIANT"
    if target.path.startswith("/current/my/"):
        return "MOODLE_MY_ROUTE"
    return "OTHER_SAME_ORIGIN"
