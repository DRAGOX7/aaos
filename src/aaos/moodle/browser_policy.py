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


def classify_browser_request(
    method: str,
    url: str,
    *,
    base_origin: str,
    canonical_wwwroot: str | None = None,
    resource_type: str | None = None,
) -> BrowserRequestDecision:
    """Allow only the COURSE_INDEX document and fixed Moodle bootstrap assets."""
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
    if _is_approved_asset(
        parsed.path,
        parsed.query,
        resource_type=resource_type,
        canonical_wwwroot=canonical_wwwroot or origin + "/current",
        expected_origin=origin,
    ):
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
    return path == "/current/my/courses.php" and not query


def _is_approved_asset(
    path: str,
    query: str,
    *,
    resource_type: str | None,
    canonical_wwwroot: str,
    expected_origin: str,
) -> bool:
    """Admit only proven static families below the validated Moodle wwwroot."""
    del query
    try:
        canonical = urlsplit(canonical_wwwroot)
    except ValueError:
        return False
    canonical_origin = f"{canonical.scheme}://{canonical.netloc}"
    base_path = canonical.path.rstrip("/")
    if (
        canonical_origin != expected_origin
        or not base_path
        or canonical.query
        or canonical.fragment
        or canonical.username
        or canonical.password
    ):
        return False
    prefix = base_path + "/"
    if not path.startswith(prefix):
        return False
    relative_path = path[len(base_path) :]
    if resource_type == "stylesheet":
        return relative_path == "/theme/styles.php" or relative_path.startswith(
            "/theme/styles.php/"
        )
    if resource_type == "script":
        return (
            relative_path == "/lib/javascript.php"
            or relative_path.startswith("/lib/javascript.php/")
            or relative_path == "/lib/requirejs.php"
            or relative_path.startswith("/lib/requirejs.php/")
            or relative_path.startswith("/lib/amd/")
            and relative_path.endswith(".js")
        )
    return False
