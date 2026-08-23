"""Private, fixed-route Playwright binding for Moodle collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from aaos.moodle.browser_models import BrowserPolicyError, BrowserRoute
from aaos.moodle.browser_policy import build_browser_url, classify_browser_request

_NAVIGATION_TIMEOUT_MS = 30_000


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
    def route(self, pattern: str, handler: object) -> None: ...

    def new_page(self) -> _Page: ...

    def close(self) -> None: ...


class PlaywrightRestrictedNavigator:
    """Expose only fixed route navigation, never raw browser primitives."""

    def __init__(self, context: _Context, base_origin: str) -> None:
        self._context = context
        self._base_origin = base_origin
        self._closed = False
        self._install_interception()

    def _install_interception(self) -> None:
        self._context.route("**/*", self._handle_request)

    def _handle_request(self, route: _Route, request: _Request) -> None:
        if (
            classify_browser_request(
                request.method, request.url, base_origin=self._base_origin
            ).code
            == "ALLOW"
        ):
            route.continue_()
        else:
            route.abort()

    @staticmethod
    def _blocked_event(*_args: object) -> None:
        raise BrowserPolicyError()

    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument:
        if self._closed:
            raise BrowserPolicyError()
        target = build_browser_url(route, base_origin=self._base_origin, identifier=identifier)
        page = self._context.new_page()
        try:
            for event in ("popup", "download", "websocket", "filechooser"):
                page.on(event, self._blocked_event)
            page.goto(target, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)
            parsed = urlsplit(page.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin != self._base_origin or parsed.path != urlsplit(target).path or parsed.query:
                raise BrowserPolicyError()
            html = page.content()
            if not isinstance(html, str):
                raise BrowserPolicyError()
            return BrowserDocument(route, origin, parsed.path, "text/html", html)
        except (OSError, TimeoutError, ValueError, TypeError):
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
