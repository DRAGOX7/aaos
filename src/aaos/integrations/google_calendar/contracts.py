from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class CanonicalCalendarEvent:
    summary: str
    start: str
    end: str
    timezone: str
    description: str | None
    location: str | None


@dataclass(frozen=True, slots=True)
class GoogleCalendarConfig:
    calendar_id: str
    expected_service_identity: str
    oauth_scope: str
    credential_path: str
    provider_adapter_version: str
    enabled: bool


def calendar_target(config: GoogleCalendarConfig) -> TargetIdentity:
    if not isinstance(config, GoogleCalendarConfig):
        raise TypeError("config must be a GoogleCalendarConfig")
    return TargetIdentity("google-calendar", config.expected_service_identity, config.calendar_id)


__all__ = ["CalendarCreateRequest", "CanonicalCalendarEvent", "GoogleCalendarConfig", "calendar_target"]
