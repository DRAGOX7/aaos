"""The fixed, site-info-only Moodle API boundary for Phase 2A."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx2

from aaos.moodle.api_parsing import SafeSiteInfo, SiteInfoParseError, parse_site_info
from aaos.moodle.auth import CredentialReadError, MoodleCredentialReader
from aaos.moodle.models import MoodleSettings

SITE_INFO_OPERATION = "core_webservice_get_site_info"
REST_ENDPOINT = "webservice/rest/server.php"
MAX_SITE_INFO_RESPONSE_BYTES = 256 * 1024

__all__ = [
    "MoodleApiError",
    "MoodleApiFailureStage",
    "MoodleApiTransport",
    "SingleUseSiteInfoProbeAuthorization",
    "SafeSiteInfo",
    "SiteInfoProbeAuthorization",
]


MoodleApiFailureStage = Literal[
    "CREDENTIAL_NOT_PERSISTED",
    "SITE_INFO_AUTHORIZATION",
    "SITE_INFO_REQUEST",
    "SITE_INFO_PARSE",
]


class MoodleApiError(RuntimeError):
    """Raised for a fixed, redacted site-info transport or protocol failure."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: MoodleApiFailureStage = "SITE_INFO_REQUEST",
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage


class SiteInfoProbeAuthorization(Protocol):
    """Injected one-shot capability supplied by the human-gated caller."""

    def consume(self) -> None:
        """Consume the externally authorized site-info probe."""


@dataclass(slots=True)
class SingleUseSiteInfoProbeAuthorization:
    """The runner's non-transferable authority for exactly one site-info call."""

    _used: bool = field(init=False, default=False, repr=False)

    def consume(self) -> None:
        if self._used:
            raise MoodleApiError(
                "Moodle site-info probe already used", failure_stage="SITE_INFO_AUTHORIZATION"
            )
        self._used = True


@dataclass(slots=True)
class MoodleApiTransport:
    settings: MoodleSettings
    canonical_wwwroot: str
    transport: httpx2.AsyncBaseTransport
    credential_reader: MoodleCredentialReader
    authorization: SiteInfoProbeAuthorization
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _site_info_used: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.canonical_wwwroot.rstrip("/"))
            _ = parsed.port
        except (AttributeError, TypeError, ValueError):
            raise MoodleApiError(
                "Moodle site-info configuration was invalid",
                failure_stage="SITE_INFO_AUTHORIZATION",
            ) from None
        normalized_wwwroot = self.canonical_wwwroot.rstrip("/")
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not normalized_wwwroot
        ):
            raise MoodleApiError(
                "Moodle site-info configuration was invalid",
                failure_stage="SITE_INFO_AUTHORIZATION",
            )
        canonical_origin = f"{parsed.scheme}://{parsed.netloc}"
        if canonical_origin != self.settings.base_origin:
            raise MoodleApiError(
                "Moodle site-info configuration was invalid",
                failure_stage="SITE_INFO_AUTHORIZATION",
            )
        self.canonical_wwwroot = normalized_wwwroot
        self._client = httpx2.AsyncClient(
            transport=self.transport,
            base_url=f"{self.canonical_wwwroot}/",
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def get_site_info(self) -> SafeSiteInfo:
        if self._closed:
            raise MoodleApiError("Moodle site-info request failed") from None
        if self._site_info_used:
            raise MoodleApiError(
                "Moodle site-info probe already used", failure_stage="SITE_INFO_AUTHORIZATION"
            ) from None
        self._site_info_used = True
        self.authorization.consume()
        try:
            token = self.credential_reader.read_api_token()
            response = await self._client.post(
                REST_ENDPOINT,
                data={
                    "wstoken": token,
                    "wsfunction": SITE_INFO_OPERATION,
                    "moodlewsrestformat": "json",
                },
            )
        except httpx2.TimeoutException:
            raise MoodleApiError("Moodle site-info request failed") from None
        except httpx2.RequestError:
            raise MoodleApiError("Moodle site-info request failed") from None
        except CredentialReadError:
            raise MoodleApiError(
                "Moodle site-info request failed", failure_stage="CREDENTIAL_NOT_PERSISTED"
            ) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise MoodleApiError("Moodle site-info request failed") from None
        if len(response.content) > MAX_SITE_INFO_RESPONSE_BYTES:
            raise MoodleApiError(
                "Moodle site-info response was invalid", failure_stage="SITE_INFO_PARSE"
            ) from None
        try:
            return parse_site_info(response.content, self.canonical_wwwroot)
        except SiteInfoParseError:
            raise MoodleApiError(
                "Moodle site-info response was invalid", failure_stage="SITE_INFO_PARSE"
            ) from None

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
