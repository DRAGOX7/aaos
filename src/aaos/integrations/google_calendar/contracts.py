from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aaos.approval.contracts import OperationId, TargetIdentity


@dataclass(frozen=True, slots=True)
class CalendarCreateRequest:
    summary: str
    start: datetime
    end: datetime
    timezone: str
    description: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not isinstance(self.start, datetime) or not isinstance(self.end, datetime) or not isinstance(self.timezone, str):
            raise TypeError("invalid calendar request fields")
        if self.description is not None and not isinstance(self.description, str): raise TypeError("description must be a string")
        if self.location is not None and not isinstance(self.location, str): raise TypeError("location must be a string")


@dataclass(frozen=True, slots=True)
class CanonicalCalendarEvent:
    summary: str
    start: str
    end: str
    timezone: str
    description: str | None
    location: str | None
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CANONICAL_TOKEN:
            raise ValueError("CanonicalCalendarEvent must be created by canonicalize_calendar_request")


@dataclass(frozen=True, slots=True)
class GoogleCalendarConfig:
    calendar_id: str
    expected_service_identity: str
    oauth_scope: str
    credential_path: str
    provider_adapter_version: str
    enabled: bool

    def __post_init__(self) -> None:
        if any(not isinstance(getattr(self, n), str) or not getattr(self, n) for n in ("calendar_id", "expected_service_identity", "oauth_scope", "credential_path", "provider_adapter_version")) or not isinstance(self.enabled, bool):
            raise TypeError("invalid Google Calendar config fields")


def calendar_target(config: GoogleCalendarConfig) -> TargetIdentity:
    if not isinstance(config, GoogleCalendarConfig):
        raise TypeError("config must be a GoogleCalendarConfig")
    # TargetIdentity intentionally uses safe logical identifiers; bind opaque service values by digest.
    import hashlib
    account = "svc-" + hashlib.sha256(config.expected_service_identity.encode()).hexdigest()[:24]
    resource = "cal-" + hashlib.sha256(config.calendar_id.encode()).hexdigest()[:24]
    return TargetIdentity("google-calendar", account, resource)


_CANONICAL_TOKEN = object()
__all__ = ["CalendarCreateRequest", "CanonicalCalendarEvent", "GoogleCalendarConfig", "calendar_target"]
