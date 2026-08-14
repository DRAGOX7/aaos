"""The fixed, site-info-only Moodle API boundary for Phase 2A."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx2

from aaos.moodle.api_parsing import SafeSiteInfo, SiteInfoParseError, parse_site_info
from aaos.moodle.auth import CredentialReadError, MoodleCredentialReader
from aaos.moodle.models import MoodleSettings

SITE_INFO_OPERATION = "core_webservice_get_site_info"
REST_ENDPOINT = "/webservice/rest/server.php"
MAX_SITE_INFO_RESPONSE_BYTES = 256 * 1024

__all__ = [
    "MoodleApiError",
    "MoodleApiTransport",
    "SafeSiteInfo",
    "SiteInfoProbeAuthorization",
]


class MoodleApiError(RuntimeError):
    """Raised for a fixed, redacted site-info transport or protocol failure."""


class SiteInfoProbeAuthorization(Protocol):
    """Injected one-shot capability supplied by the human-gated caller."""

    def consume(self) -> None:
        """Consume the externally authorized site-info probe."""


@dataclass(slots=True)
class MoodleApiTransport:
    settings: MoodleSettings
    transport: httpx2.AsyncBaseTransport
    credential_reader: MoodleCredentialReader
    authorization: SiteInfoProbeAuthorization
    _client: httpx2.AsyncClient = field(init=False, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _site_info_used: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx2.AsyncClient(
            transport=self.transport,
            base_url=self.settings.base_origin,
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def get_site_info(self) -> SafeSiteInfo:
        if self._closed:
            raise MoodleApiError("Moodle site-info request failed") from None
        if self._site_info_used:
            raise MoodleApiError("Moodle site-info probe already used") from None
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
            raise MoodleApiError("Moodle site-info request failed") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise MoodleApiError("Moodle site-info request failed") from None
        if len(response.content) > MAX_SITE_INFO_RESPONSE_BYTES:
            raise MoodleApiError("Moodle site-info response was invalid") from None
        try:
            return parse_site_info(response.content, self.settings.base_origin)
        except SiteInfoParseError:
            raise MoodleApiError("Moodle site-info response was invalid") from None

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.aclose()
