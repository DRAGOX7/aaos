"""Continue a remembered Microsoft sign-in from Moodle's login page.

Spec: docs/superpowers/specs/2026-10-03-phase-10-sso-continuation.md.

Moodle's session ends when the browser closes, so each sync starts on the
login page, whose only action is "Log in with Microsoft". When the browser
profile's Microsoft sign-in is remembered, following that link once is
enough. This module only reads the page and navigates to that one same-origin
link: it never types, clicks, reads or stores anything on any sign-in page.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from html.parser import HTMLParser
from time import monotonic as _monotonic
from typing import Protocol
from urllib.parse import urljoin, urlsplit

LOGIN_PATH = "/current/login/index.php"
SSO_PATH = "/current/auth/oauth2/login.php"
_SIGN_IN_PATHS = ("/current/login/", "/current/auth/")
_POLL_MILLISECONDS = 250


class SignInResult(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"  # not on the login page, or no single SSO link
    CONTINUED = "CONTINUED"  # back on Moodle and sent to the course list
    TIMED_OUT = "TIMED_OUT"  # sign-in did not return to Moodle within the budget


class _SignInPage(Protocol):
    url: str

    def content(self) -> str: ...

    def goto(self, url: str, **kwargs: object) -> object: ...

    def wait_for_timeout(self, milliseconds: int) -> object: ...


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def find_sso_href(html: str, page_url: str, base_origin: str) -> str | None:
    """The one same-origin "Log in with Microsoft" link, or None."""
    parser = _Anchors()
    parser.feed(html)
    found = set()
    for href in parser.hrefs:
        absolute = urljoin(page_url, href)
        parts = urlsplit(absolute)
        if _origin(absolute) == _origin(base_origin) and parts.path == SSO_PATH:
            found.add(absolute)
    return found.pop() if len(found) == 1 else None


def _is_login_page(url: str, base_origin: str) -> bool:
    return _origin(url) == _origin(base_origin) and urlsplit(url).path == LOGIN_PATH


def _back_on_moodle(url: str, base_origin: str) -> bool:
    path = urlsplit(url).path
    return _origin(url) == _origin(base_origin) and not path.startswith(_SIGN_IN_PATHS)


def continue_remembered_sign_in(
    page: _SignInPage,
    *,
    base_origin: str,
    course_index_url: str,
    timeout_seconds: float,
    monotonic: Callable[[], float] = _monotonic,
) -> SignInResult:
    """Follow the SSO link once; if sign-in returns to Moodle, open the course list."""
    deadline = monotonic() + timeout_seconds
    if not _is_login_page(page.url, base_origin):
        return SignInResult.NOT_ATTEMPTED
    href = find_sso_href(page.content(), page.url, base_origin)
    if href is None:
        return SignInResult.NOT_ATTEMPTED
    page.goto(href, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
    # Sign-in may pass through Microsoft or a federated university page. AAOS
    # takes no action there; it only waits for the redirect back to Moodle.
    while monotonic() < deadline:
        if _back_on_moodle(page.url, base_origin):
            remaining = max(1, int((deadline - monotonic()) * 1000))
            page.goto(course_index_url, wait_until="domcontentloaded", timeout=remaining)
            return SignInResult.CONTINUED
        page.wait_for_timeout(_POLL_MILLISECONDS)
    return SignInResult.TIMED_OUT
