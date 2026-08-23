"""Pure allowlist policy for restricted Moodle collection requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from aaos.moodle.browser_models import BrowserPolicyError, BrowserRoute


@dataclass(frozen=True, slots=True)
class BrowserRequestDecision:
    code: Literal["ALLOW", "BLOCK"]


def build_browser_url(
    route: BrowserRoute, *, base_origin: str, identifier: int | None = None
) -> str:
    """Return one module-owned document URL or reject the route request."""
    origin = _validate_origin(base_origin)
    if route == "COURSE_INDEX" and identifier is None:
        return f"{origin}/current/my/courses.php"
    if route == "COURSE_DETAIL" and _positive_identifier(identifier):
        return f"{origin}/current/course/view.php?id={identifier}"
    if route == "ASSIGNMENT_LIST" and _positive_identifier(identifier):
        return f"{origin}/current/mod/assign/index.php?id={identifier}"
    if route == "ANNOUNCEMENT_LIST" and _positive_identifier(identifier):
        return f"{origin}/current/mod/forum/view.php?id={identifier}"
    raise BrowserPolicyError()


def classify_browser_request(method: str, url: str, *, base_origin: str) -> BrowserRequestDecision:
    """Allow only fixed same-origin document routes and static GET/HEAD assets."""
    if method not in {"GET", "HEAD"}:
        return BrowserRequestDecision("BLOCK")
    try:
        origin = _validate_origin(base_origin)
        parsed = urlsplit(url)
    except (BrowserPolicyError, ValueError):
        return BrowserRequestDecision("BLOCK")
    if (
        f"{parsed.scheme}://{parsed.netloc}" != origin
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return BrowserRequestDecision("BLOCK")
    if _is_approved_document(parsed.path, parse_qs(parsed.query, keep_blank_values=True)):
        return BrowserRequestDecision("ALLOW")
    if _is_approved_asset(parsed.path, parsed.query):
        return BrowserRequestDecision("ALLOW")
    return BrowserRequestDecision("BLOCK")


def _validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise BrowserPolicyError()
    return f"https://{parsed.netloc}"


def _positive_identifier(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_approved_document(path: str, query: dict[str, list[str]]) -> bool:
    if path == "/current/my/courses.php":
        return not query
    return (
        path
        in {
            "/current/course/view.php",
            "/current/mod/assign/index.php",
            "/current/mod/forum/view.php",
        }
        and set(query) == {"id"}
        and len(query["id"]) == 1
        and query["id"][0].isdigit()
        and int(query["id"][0]) > 0
    )


def _is_approved_asset(path: str, query: str) -> bool:
    return (
        not query
        and path.startswith("/current/theme/")
        and path.endswith((".css", ".js", ".png", ".svg", ".woff2"))
    )
