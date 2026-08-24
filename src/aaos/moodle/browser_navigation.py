"""Private, fixed-route Playwright binding for Moodle collection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from aaos.moodle.browser_models import BrowserPolicyError, BrowserRoute
from aaos.moodle.browser_policy import build_browser_url, classify_browser_request

_NAVIGATION_TIMEOUT_MS = 30_000
_FRONTEND_SETTLE_TIMEOUT_SECONDS = 15
_FRONTEND_SETTLE_POLL_MILLISECONDS = 250
_FRONTEND_COURSE_AJAX_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"
_MAX_FRONTEND_AJAX_BODY_BYTES = 16_384
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
BootstrapRouteClass = Literal[
    "STATIC_LIB_FILE",
    "STATIC_AMD_BUILD_FILE",
    "STATIC_THEME_FILE",
    "THEME_IMAGE_HANDLER",
    "CORE_PIX_ASSET",
    "STATIC_PLUGIN_ASSET",
    "MOODLE_HANDLER_OTHER",
    "ACADEMIC_ROUTE",
    "AJAX_SERVICE",
    "PLUGINFILE",
    "UNKNOWN_MOODLE_ROUTE",
    "OTHER_ORIGIN",
]
MoodleHandlerClass = Literal["MOODLE_YUI_COMBO_HANDLER", "MOODLE_HANDLER_OTHER"]
HandlerStaticEligibility = Literal["VALIDATED_STATIC", "AMBIGUOUS"]


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
    other_moodle_resource_counts: tuple[tuple[RequestResourceType, int], ...] = ()
    other_moodle_method_counts: tuple[tuple[RequestMethodClass, int], ...] = ()
    other_origin_resource_counts: tuple[tuple[RequestResourceType, int], ...] = ()
    bootstrap_route_counts: tuple[tuple[BootstrapRouteClass, int], ...] = ()
    moodle_handler_resource_counts: tuple[
        tuple[MoodleHandlerClass, RequestResourceType, int], ...
    ] = ()


class _RequestObserver:
    def __init__(self) -> None:
        self._same_origin_xhr_fetch_count = 0
        self._moodle_ajax_service_count = 0
        self._blocked_post_moodle_ajax_service_count = 0
        self._allowed_static_request_count = 0
        self._static_counts: dict[StaticAssetClass, list[int]] = {}
        self._other_moodle_resource_counts: dict[RequestResourceType, int] = {}
        self._other_moodle_method_counts: dict[RequestMethodClass, int] = {}
        self._other_origin_resource_counts: dict[RequestResourceType, int] = {}
        self._bootstrap_route_counts: dict[BootstrapRouteClass, int] = {}
        self._moodle_handler_resource_counts: dict[
            tuple[MoodleHandlerClass, RequestResourceType], int
        ] = {}

    def record(
        self,
        observation: BrowserRequestObservation,
        *,
        url: object | None = None,
        canonical_wwwroot: str | None = None,
    ) -> None:
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
        if observation.origin == "OTHER_ORIGIN":
            self._other_origin_resource_counts[observation.resource_type] = (
                self._other_origin_resource_counts.get(observation.resource_type, 0) + 1
            )
        if (
            observation.origin == "MOODLE_SAME_ORIGIN"
            and observation.route == "OTHER_MOODLE_ROUTE"
            and observation.policy_result == "BLOCKED"
        ):
            self._other_moodle_resource_counts[observation.resource_type] = (
                self._other_moodle_resource_counts.get(observation.resource_type, 0) + 1
            )
            self._other_moodle_method_counts[observation.method] = (
                self._other_moodle_method_counts.get(observation.method, 0) + 1
            )
            route_class = classify_bootstrap_route_request(url, canonical_wwwroot)
            self._bootstrap_route_counts[route_class] = (
                self._bootstrap_route_counts.get(route_class, 0) + 1
            )
            if route_class == "MOODLE_HANDLER_OTHER":
                handler_class = classify_moodle_handler_request(url, canonical_wwwroot)
                key = (handler_class, observation.resource_type)
                self._moodle_handler_resource_counts[key] = (
                    self._moodle_handler_resource_counts.get(key, 0) + 1
                )

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
            other_moodle_resource_counts=tuple(sorted(self._other_moodle_resource_counts.items())),
            other_moodle_method_counts=tuple(sorted(self._other_moodle_method_counts.items())),
            other_origin_resource_counts=tuple(sorted(self._other_origin_resource_counts.items())),
            bootstrap_route_counts=tuple(sorted(self._bootstrap_route_counts.items())),
            moodle_handler_resource_counts=tuple(
                (handler_class, resource_type, count)
                for (handler_class, resource_type), count in sorted(
                    self._moodle_handler_resource_counts.items()
                )
            ),
        )


class RestrictedBrowserNavigator(Protocol):
    def open_course_index_document(self) -> BrowserDocument: ...

    def open_frontend_course_index(self) -> BrowserDocument: ...

    def close(self) -> None: ...


class _Route(Protocol):
    def abort(self) -> None: ...

    def continue_(self) -> None: ...


class _Request(Protocol):
    method: str
    url: str
    resource_type: str
    post_data: str | None


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
        self._blocked_frontend_course_ajax = False
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
        allowed = self._request_is_allowed(request)
        observation = classify_request_observation(
            request.method,
            request.url,
            request.resource_type,
            base_origin=self._base_origin,
            canonical_wwwroot=self._canonical_wwwroot,
            has_body=getattr(request, "post_data", None) is not None,
            policy_result="ALLOWED" if allowed else "BLOCKED",
        )
        self._request_observer.record(
            observation,
            url=request.url,
            canonical_wwwroot=self._canonical_wwwroot,
        )
        self._request_observer.record_static(
            classify_static_asset_request(request.url, self._canonical_wwwroot),
            observation.policy_result,
        )
        if allowed:
            route.continue_()
        else:
            self._failure_stage = "REQUEST_POLICY"
            if _is_moodle_ajax_post(request.method, request.url, self._canonical_wwwroot):
                self._blocked_frontend_course_ajax = True
            route.abort()

    def _request_is_allowed(self, request: _Request) -> bool:
        """Permit static reads plus one live-proven Moodle frontend operation."""
        if (
            classify_browser_request(
                request.method,
                request.url,
                base_origin=self._base_origin,
                canonical_wwwroot=self._canonical_wwwroot,
                resource_type=request.resource_type,
                has_body=getattr(request, "post_data", None) is not None,
            ).code
            == "ALLOW"
        ):
            return True
        return is_approved_frontend_course_ajax_request(
            request.method,
            request.url,
            request.resource_type,
            getattr(request, "post_data", None),
            base_origin=self._base_origin,
            canonical_wwwroot=self._canonical_wwwroot,
        )

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

    def open_course_index_document(self) -> BrowserDocument:
        """Read only the fixed COURSE_INDEX document for diagnostics."""
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
            html = page.content()
            if not isinstance(html, str):
                raise BrowserPolicyError()
            return BrowserDocument("COURSE_INDEX", origin, parsed.path, "text/html", html)
        except (OSError, TimeoutError, ValueError, TypeError):
            if self._failure_stage == "NONE":
                self._failure_stage = "NAVIGATION"
            raise BrowserPolicyError() from None
        finally:
            self._close_owned_page(page)

    def open_frontend_course_index(self) -> BrowserDocument:
        """Wait only for the normal Moodle page lifecycle, then return its bounded DOM."""
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
            deadline = monotonic() + _FRONTEND_SETTLE_TIMEOUT_SECONDS
            html = ""
            while monotonic() < deadline:
                html = page.content()
                if _is_frontend_course_index_settled(html, self._base_origin):
                    return BrowserDocument("COURSE_INDEX", origin, parsed.path, "text/html", html)
                if self._blocked_frontend_course_ajax:
                    raise BrowserPolicyError()
                page.wait_for_timeout(_FRONTEND_SETTLE_POLL_MILLISECONDS)
            if self._failure_stage == "NONE":
                self._failure_stage = "NAVIGATION"
            raise BrowserPolicyError()
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


def is_approved_frontend_course_ajax_request(
    method: object,
    url: object,
    resource_type: object,
    post_data: object,
    *,
    base_origin: str,
    canonical_wwwroot: str,
) -> bool:
    """Recognize the one Moodle-owned read operation proven by the live page.

    The operation's argument and query serialization remains Moodle-owned. This
    function reads only enough JSON structure to prove one operation and its
    method identity; it neither reads nor retains argument values.
    """
    if (
        method != "POST"
        or resource_type not in {"xhr", "fetch"}
        or not isinstance(url, str)
        or not isinstance(post_data, str)
        or len(post_data.encode("utf-8")) > _MAX_FRONTEND_AJAX_BODY_BYTES
    ):
        return False
    try:
        target = urlsplit(url)
        canonical = urlsplit(canonical_wwwroot)
        payload = json.loads(post_data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    canonical_origin = f"{canonical.scheme}://{canonical.netloc}"
    target_origin = f"{target.scheme}://{target.netloc}"
    base_path = canonical.path.rstrip("/")
    if (
        canonical.scheme != "https"
        or not canonical.netloc
        or canonical.query
        or canonical.fragment
        or canonical.username
        or canonical.password
        or target.scheme != "https"
        or not target.netloc
        or target.fragment
        or target.username
        or target.password
        or canonical_origin != base_origin
        or target_origin != base_origin
        or target.path != base_path + "/lib/ajax/service.php"
        or not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
        or payload[0].get("methodname") != _FRONTEND_COURSE_AJAX_METHOD
    ):
        return False
    return True


def _is_moodle_ajax_post(method: object, url: object, canonical_wwwroot: str) -> bool:
    if method != "POST" or not isinstance(url, str):
        return False
    try:
        target = urlsplit(url)
        canonical = urlsplit(canonical_wwwroot)
    except ValueError:
        return False
    return target.path == canonical.path.rstrip("/") + "/lib/ajax/service.php"


def _is_frontend_course_index_settled(html: object, expected_origin: str) -> bool:
    """Recognize a canonical course link or Moodle's explicit empty-course state."""
    from aaos.moodle.browser_parsing import (
        has_frontend_empty_course_state,
        parse_frontend_course_index,
    )

    try:
        if parse_frontend_course_index(html, expected_origin=expected_origin):
            return True
        return has_frontend_empty_course_state(html)
    except BrowserPolicyError:
        return False


def classify_request_observation(
    method: object,
    url: object,
    resource_type: object,
    *,
    base_origin: str,
    canonical_wwwroot: str | None = None,
    has_body: bool = False,
    policy_result: RequestPolicyResult | None = None,
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
            has_body=has_body,
        ).code
        == "ALLOW"
    ):
        route_class = "STATIC_ASSET"
    else:
        route_class = "OTHER_MOODLE_ROUTE"
    resolved_policy_result: RequestPolicyResult = policy_result or (
        "ALLOWED"
        if isinstance(method, str)
        and classify_browser_request(
            method,
            safe_url,
            base_origin=base_origin,
            canonical_wwwroot=canonical_wwwroot,
            resource_type=resource_type if isinstance(resource_type, str) else None,
            has_body=has_body,
        ).code
        == "ALLOW"
        else "BLOCKED"
    )
    return BrowserRequestObservation(
        method=method_class,
        origin=origin_class,
        route=route_class,
        resource_type=resource_class,
        policy_result=resolved_policy_result,
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


def classify_bootstrap_route_request(
    url: object, canonical_wwwroot: str | None
) -> BootstrapRouteClass:
    """Classify a blocked request structurally without retaining its address."""
    if not isinstance(url, str) or not isinstance(canonical_wwwroot, str):
        return "OTHER_ORIGIN"
    try:
        target = urlsplit(url)
        base = urlsplit(canonical_wwwroot)
    except ValueError:
        return "OTHER_ORIGIN"
    if (
        target.scheme != "https"
        or base.scheme != "https"
        or not target.netloc
        or not base.netloc
        or f"{target.scheme}://{target.netloc}" != f"{base.scheme}://{base.netloc}"
    ):
        return "OTHER_ORIGIN"
    base_path = base.path.rstrip("/")
    prefix = base_path + "/"
    if not base_path or base.query or base.fragment or not target.path.startswith(prefix):
        return "UNKNOWN_MOODLE_ROUTE"
    path = target.path[len(base_path) :]
    if path == "/pluginfile.php" or path.startswith("/pluginfile.php/"):
        return "PLUGINFILE"
    if path.startswith("/lib/ajax/"):
        return "AJAX_SERVICE"
    if path == "/theme/image.php" or path.startswith("/theme/image.php/"):
        return "THEME_IMAGE_HANDLER"
    if (
        path.startswith("/mod/")
        and "/pix/" in path
        and path.endswith((".png", ".svg", ".jpg", ".webp"))
    ):
        return "STATIC_PLUGIN_ASSET"
    if path.startswith(("/course/", "/mod/", "/my/", "/user/")):
        return "ACADEMIC_ROUTE"
    if path.endswith(".php") or ".php/" in path:
        return "MOODLE_HANDLER_OTHER"
    if path.startswith("/lib/amd/build/") and path.endswith(".js"):
        return "STATIC_AMD_BUILD_FILE"
    if path.startswith("/lib/") and path.endswith((".js", ".css", ".png", ".svg", ".jpg", ".webp")):
        return "STATIC_LIB_FILE"
    if path.startswith("/theme/") and path.endswith(
        (".js", ".css", ".png", ".svg", ".jpg", ".webp")
    ):
        return "STATIC_THEME_FILE"
    if path.startswith("/pix/") and path.endswith((".png", ".svg", ".jpg", ".webp")):
        return "CORE_PIX_ASSET"
    return "UNKNOWN_MOODLE_ROUTE"


def classify_moodle_handler_request(
    url: object, canonical_wwwroot: str | None
) -> MoodleHandlerClass:
    """Recognize only exact, public-Moodle infrastructure handlers."""
    if not isinstance(url, str) or not isinstance(canonical_wwwroot, str):
        return "MOODLE_HANDLER_OTHER"
    try:
        target = urlsplit(url)
        base = urlsplit(canonical_wwwroot)
    except ValueError:
        return "MOODLE_HANDLER_OTHER"
    base_path = base.path.rstrip("/")
    if (
        target.scheme != "https"
        or base.scheme != "https"
        or not target.netloc
        or not base.netloc
        or f"{target.scheme}://{target.netloc}" != f"{base.scheme}://{base.netloc}"
        or not base_path
        or base.query
        or base.fragment
        or target.path != base_path + "/theme/yui_combo.php"
    ):
        return "MOODLE_HANDLER_OTHER"
    return "MOODLE_YUI_COMBO_HANDLER"


def handler_static_eligibility(handler_class: MoodleHandlerClass) -> HandlerStaticEligibility:
    """Return source-verified eligibility without authorizing the request."""
    if handler_class == "MOODLE_YUI_COMBO_HANDLER":
        return "VALIDATED_STATIC"
    return "AMBIGUOUS"


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
