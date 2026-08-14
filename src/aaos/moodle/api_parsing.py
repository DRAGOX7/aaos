"""Safe parsing for the single Phase 2A Moodle site-info response."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit


class SiteInfoParseError(ValueError):
    """Raised when the provider response is not a safe site-info document."""


@dataclass(frozen=True, slots=True)
class SafeSiteInfo:
    canonical_wwwroot: str
    release: str | None
    available_functions: tuple[str, ...]


def parse_site_info(payload: bytes, expected_origin: str) -> SafeSiteInfo:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SiteInfoParseError("Moodle site-info response was invalid") from None
    if not isinstance(document, dict):
        raise SiteInfoParseError("Moodle site-info response was invalid")

    siteurl = document.get("siteurl")
    if not isinstance(siteurl, str):
        raise SiteInfoParseError("Moodle site-info response was invalid")
    normalized_siteurl = siteurl.rstrip("/")
    try:
        parsed = urlsplit(normalized_siteurl)
        _ = parsed.port
    except (TypeError, ValueError):
        raise SiteInfoParseError("Moodle site-info response was invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or normalized_siteurl != expected_origin.rstrip("/")
    ):
        raise SiteInfoParseError("Moodle site-info response was invalid")

    functions_value = document.get("functions")
    if not isinstance(functions_value, list):
        raise SiteInfoParseError("Moodle site-info response was invalid")
    function_names: list[str] = []
    for function in functions_value:
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise SiteInfoParseError("Moodle site-info response was invalid")
        name = function["name"].strip()
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise SiteInfoParseError("Moodle site-info response was invalid")
        if name in function_names:
            raise SiteInfoParseError("Moodle site-info response was invalid")
        function_names.append(name)
    if "core_webservice_get_site_info" not in function_names:
        raise SiteInfoParseError("Moodle site-info response was invalid")

    release = document.get("release")
    if release is not None and (
        not isinstance(release, str)
        or not release.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in release)
    ):
        raise SiteInfoParseError("Moodle site-info response was invalid")
    return SafeSiteInfo(
        canonical_wwwroot=normalized_siteurl,
        release=release.strip() if isinstance(release, str) else None,
        available_functions=tuple(function_names),
    )
