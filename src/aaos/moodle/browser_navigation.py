"""Private, fixed-route Playwright binding for Moodle collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
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


@dataclass(frozen=True, slots=True)
class BrowserDocument:
    route: BrowserRoute
    final_origin: str
    final_path: str
    content_type: str
    html: str


class RestrictedBrowserNavigator(Protocol):
    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument: ...

    def close(self) -> None: ...


class _Route(Protocol):
    def abort(self) -> None: ...

    def continue_(self) -> None: ...


class _Request(Protocol):
    method: str
    url: str


class _Page(Protocol):
    url: str

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object: ...

    def content(self) -> str: ...

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
        self, context: _Context, base_origin: str, *, transition_sentinel: _Page | None = None
    ) -> None:
        self._context = context
        self._base_origin = base_origin
        self._transition_sentinel = transition_sentinel
        self._closed = False
        self._failure_stage: FailureStage = "NONE"
        self._redirect_class: RedirectClass | None = None
        self._install_interception()

    @property
    def failure_stage(self) -> FailureStage:
        """Return only a fixed non-sensitive failure classification."""
        return self._failure_stage

    @property
    def redirect_class(self) -> RedirectClass | None:
        """Return the safe class of an unexpected final navigation, if any."""
        return self._redirect_class

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
        if (
            classify_browser_request(
                request.method, request.url, base_origin=self._base_origin
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
            page.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._context.close()
        except (OSError, ValueError, TypeError):
            raise BrowserPolicyError() from None


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
