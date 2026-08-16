"""Typed, unauthenticated Moodle mobile public-configuration probe."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import parse_qs, urlsplit

import httpx2

from aaos.moodle.models import MoodleSettings

PUBLIC_CONFIG_OPERATION: Final = "tool_mobile_get_public_config"
PUBLIC_CONFIG_LANGUAGE: Final = "en"
AJAX_ENDPOINT: Final = "/lib/ajax/service-nologin.php"
MAX_PUBLIC_CONFIG_RESPONSE_BYTES: Final = 256 * 1024


class MoodlePublicConfigError(RuntimeError):
    """Raised for a fixed, redacted public-configuration failure."""

    def __init__(self, message: str, *, category: str, code: str = "invalid_response") -> None:
        super().__init__(message)
        self.category = category
        self.code = code


@dataclass(frozen=True, slots=True)
class PublicIdentityProvider:
    name: str
    oauth_provider_id: int | None


@dataclass(frozen=True, slots=True)
class MoodlePublicConfig:
    canonical_wwwroot: str
    httpswwwroot: str
    enablewebservices: bool
    enablemobilewebservice: bool
    typeoflogin: int
    launchurl: str | None
    identity_providers: tuple[PublicIdentityProvider, ...]


def _failure() -> MoodlePublicConfigError:
    return MoodlePublicConfigError(
        "Moodle public configuration request failed", category="request", code="request_failed"
    )


def _invalid(code: str = "invalid_response") -> MoodlePublicConfigError:
    return MoodlePublicConfigError(
        "Moodle public configuration response was invalid", category="malformed", code=code
    )


def _ajax_error() -> MoodlePublicConfigError:
    return MoodlePublicConfigError(
        "Moodle public configuration reported an AJAX error",
        category="ajax_error",
        code="ajax_error",
    )


def _safe_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > maximum or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
    ):
        raise _invalid()
    return normalized


def _expected_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.rstrip("/"))
        _ = parsed.port
    except (TypeError, ValueError):
        raise _invalid() from None
    if parsed.scheme != "https" or parsed.hostname is None or parsed.username or parsed.password:
        raise _invalid()
    return f"{parsed.scheme}://{parsed.netloc}"


def _canonical_parts(value: str) -> tuple[str, str]:
    normalized = _safe_text(value, maximum=4096).rstrip("/")
    try:
        parsed = urlsplit(normalized)
        _ = parsed.port
    except (TypeError, ValueError):
        raise _invalid() from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise _invalid()
    return normalized, f"{parsed.scheme}://{parsed.netloc}"


def _https_url(value: object, expected_wwwroot: str, *, exact_path: bool = False) -> str:
    normalized = _safe_text(value, maximum=4096).rstrip("/")
    expected_canonical, expected_origin = _canonical_parts(expected_wwwroot)
    try:
        parsed = urlsplit(normalized)
        _ = parsed.port
    except (TypeError, ValueError):
        raise _invalid() from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or f"{parsed.scheme}://{parsed.netloc}" != expected_origin
        or (exact_path and normalized != expected_canonical)
    ):
        raise _invalid()
    return normalized


def _binary_flag(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1}:
        raise _invalid()
    return bool(value)


def _parse_flag(value: object, field: str) -> bool:
    try:
        return _binary_flag(value)
    except MoodlePublicConfigError:
        raise _invalid(f"invalid_{field}") from None


def _provider(value: object, expected_origin: str) -> PublicIdentityProvider:
    if not isinstance(value, dict):
        raise _invalid("invalid_provider_entry")
    try:
        name = _safe_text(value.get("name"), maximum=256)
    except MoodlePublicConfigError:
        raise _invalid("invalid_provider_name") from None
    try:
        url = _safe_text(value.get("url"), maximum=4096).rstrip("/")
    except MoodlePublicConfigError:
        raise _invalid("invalid_provider_url") from None
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except (TypeError, ValueError):
        raise _invalid("invalid_provider_url") from None
    query = parse_qs(parsed.query, keep_blank_values=True)
    identifiers = query.get("id")
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _invalid("invalid_provider_url")
    if f"{parsed.scheme}://{parsed.netloc}" != _expected_origin(expected_origin):
        raise _invalid("provider_cross_origin")
    if identifiers is None:
        return PublicIdentityProvider(name=name, oauth_provider_id=None)
    if set(query) != {"id"} or not identifiers:
        raise _invalid("provider_invalid_id")
    if len(identifiers) != 1 or not identifiers[0].isdigit() or int(identifiers[0]) <= 0:
        raise _invalid("provider_invalid_id")
    return PublicIdentityProvider(name=name, oauth_provider_id=int(identifiers[0]))


def parse_public_config(payload: bytes, expected_origin: str) -> MoodlePublicConfig:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise _invalid() from None
    if isinstance(document, dict):
        if document.get("error") is True:
            raise _ajax_error()
        raise _invalid()
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise _invalid()
    response = document[0]
    error_value = response.get("error", False)
    if error_value is True:
        raise _ajax_error()
    if (error_value is not False and error_value is not None) or not isinstance(
        response.get("data"), dict
    ):
        raise _invalid()
    document = response["data"]
    expected_canonical, expected_origin_value = _canonical_parts(expected_origin)
    try:
        wwwroot = _https_url(document.get("wwwroot"), expected_canonical, exact_path=True)
    except MoodlePublicConfigError:
        raise _invalid("invalid_wwwroot") from None
    try:
        httpswwwroot = _https_url(document.get("httpswwwroot"), expected_canonical, exact_path=True)
    except MoodlePublicConfigError:
        raise _invalid("invalid_httpswwwroot") from None
    if wwwroot != httpswwwroot:
        raise _invalid()
    launch_value = document.get("launchurl")
    if launch_value is None:
        launchurl = None
    else:
        try:
            launchurl = _https_url(launch_value, expected_canonical)
        except MoodlePublicConfigError:
            raise _invalid("invalid_launchurl") from None
    providers_value = document.get("identityproviders", [])
    if not isinstance(providers_value, list):
        raise _invalid("invalid_identityproviders_type")
    providers = tuple(_provider(item, expected_origin_value) for item in providers_value)
    provider_ids = [
        provider.oauth_provider_id
        for provider in providers
        if provider.oauth_provider_id is not None
    ]
    if len(set(provider_ids)) != len(provider_ids):
        raise _invalid("provider_duplicate_id")
    typeoflogin = document.get("typeoflogin")
    if (
        isinstance(typeoflogin, bool)
        or not isinstance(typeoflogin, int)
        or typeoflogin not in {1, 2, 3}
    ):
        raise _invalid("invalid_typeoflogin")
    return MoodlePublicConfig(
        canonical_wwwroot=wwwroot,
        httpswwwroot=httpswwwroot,
        enablewebservices=_parse_flag(document.get("enablewebservices"), "enablewebservices"),
        enablemobilewebservice=_parse_flag(
            document.get("enablemobilewebservice"), "enablemobilewebservice"
        ),
        typeoflogin=typeoflogin,
        launchurl=launchurl,
        identity_providers=providers,
    )


@dataclass(slots=True)
class MoodlePublicConfigTransport:
    settings: MoodleSettings
    canonical_wwwroot: str
    transport: httpx2.AsyncBaseTransport
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _used: bool = field(init=False, default=False, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        try:
            canonical, origin = _canonical_parts(self.canonical_wwwroot)
            if urlsplit(canonical).path != "/current" or origin != _expected_origin(
                self.settings.base_origin
            ):
                raise _invalid()
            self.canonical_wwwroot = canonical
        except MoodlePublicConfigError:
            raise
        except (TypeError, ValueError):
            raise _invalid() from None
        self._client = httpx2.AsyncClient(
            transport=self.transport,
            base_url=self.canonical_wwwroot,
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def get_public_config(self) -> MoodlePublicConfig:
        if self._closed or self._used:
            raise _failure()
        self._used = True
        try:
            request = self._client.build_request(
                "POST",
                AJAX_ENDPOINT,
                params={"info": PUBLIC_CONFIG_OPERATION, "lang": PUBLIC_CONFIG_LANGUAGE},
                json=[{"index": 0, "methodname": PUBLIC_CONFIG_OPERATION, "args": {}}],
            )
            response = await self._client.send(request, stream=True)
            try:
                if response.status_code < 200 or response.status_code >= 300:
                    raise _failure()
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "application/json":
                    raise _invalid()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PUBLIC_CONFIG_RESPONSE_BYTES:
                        raise _invalid()
                    chunks.append(chunk)
                payload = b"".join(chunks)
            finally:
                await response.aclose()
        except (httpx2.TimeoutException, httpx2.RequestError):
            raise _failure() from None
        try:
            return parse_public_config(payload, self.canonical_wwwroot)
        except MoodlePublicConfigError:
            raise

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
