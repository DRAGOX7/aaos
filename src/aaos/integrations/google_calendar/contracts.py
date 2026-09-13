from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aaos.approval.contracts import TargetIdentity


@dataclass(frozen=True, slots=True)
class CalendarCreateRequest:
    summary: str
    start: datetime
    end: datetime
    timezone: str
    description: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.summary, str)
            or not isinstance(self.start, datetime)
            or not isinstance(self.end, datetime)
            or not isinstance(self.timezone, str)
        ):
            raise TypeError("invalid calendar request fields")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("description must be a string")
        if self.location is not None and not isinstance(self.location, str):
            raise TypeError("location must be a string")


@dataclass(frozen=True, slots=True, init=False)
class CanonicalCalendarEvent:
    summary: str
    start: str
    end: str
    timezone: str
    description: str | None
    location: str | None

    @classmethod
    def _create(
        cls,
        summary: str,
        start: str,
        end: str,
        timezone: str,
        description: str | None,
        location: str | None,
    ) -> "CanonicalCalendarEvent":
        value = object.__new__(cls)
        for name, item in (
            ("summary", summary),
            ("start", start),
            ("end", end),
            ("timezone", timezone),
            ("description", description),
            ("location", location),
        ):
            object.__setattr__(value, name, item)
        return value

    def __post_init__(self) -> None:
        if not all(
            isinstance(getattr(self, n), str) and getattr(self, n)
            for n in ("summary", "start", "end", "timezone")
        ):
            raise TypeError("invalid canonical event fields")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("description must be a string")
        if self.location is not None and not isinstance(self.location, str):
            raise TypeError("location must be a string")


@dataclass(frozen=True, slots=True)
class GoogleCalendarConfig:
    calendar_id: str
    expected_service_identity: str
    oauth_scope: str
    credential_path: str
    provider_adapter_version: str
    enabled: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(getattr(self, n), str) or not getattr(self, n)
            for n in (
                "calendar_id",
                "expected_service_identity",
                "oauth_scope",
                "credential_path",
                "provider_adapter_version",
            )
        ) or not isinstance(self.enabled, bool):
            raise TypeError("invalid Google Calendar config fields")
        if self.oauth_scope != "https://www.googleapis.com/auth/calendar.events":
            raise ValueError("oauth_scope must be the calendar events scope")


def calendar_target(config: GoogleCalendarConfig) -> TargetIdentity:
    if not isinstance(config, GoogleCalendarConfig):
        raise TypeError("config must be a GoogleCalendarConfig")
    # TargetIdentity intentionally uses safe logical identifiers; bind opaque service values by digest.
    import hashlib

    account = "svc-" + hashlib.sha256(config.expected_service_identity.encode()).hexdigest()[:24]
    resource = "cal-" + hashlib.sha256(config.calendar_id.encode()).hexdigest()[:24]
    return TargetIdentity("google-calendar", account, resource)


__all__ = [
    "CalendarCreateRequest",
    "CanonicalCalendarEvent",
    "GoogleCalendarConfig",
    "calendar_target",
]
