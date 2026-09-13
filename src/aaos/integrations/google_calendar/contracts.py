from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aaos.approval.contracts import TargetIdentity

_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_CONFIG_VALUE_LIMIT = 4096
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _canonical_text(
    value: object,
    name: str,
    limit: int,
    *,
    optional: bool = False,
) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if (
        not value
        or len(value) > limit
        or unicodedata.normalize("NFKC", value) != value
        or _has_control(value)
    ):
        raise ValueError(f"{name} is not canonical")


def _canonical_instant(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not _RFC3339_UTC.fullmatch(value):
        raise ValueError(f"{name} must be canonical RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be canonical RFC3339 UTC") from error
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise ValueError(f"{name} must be canonical RFC3339 UTC")
    return parsed


def _config_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > _CONFIG_VALUE_LIMIT or _has_control(value):
        raise ValueError(f"{name} is invalid or exceeds its limit")
    return value


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

    def __init__(self) -> None:
        raise TypeError("CanonicalCalendarEvent must be created by canonicalize_calendar_request")

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
        value._validate()
        return value

    def _validate(self) -> None:
        _canonical_text(self.summary, "summary", 200)
        _canonical_text(self.description, "description", 2000, optional=True)
        _canonical_text(self.location, "location", 300, optional=True)
        start = _canonical_instant(self.start, "start")
        end = _canonical_instant(self.end, "end")
        if start >= end:
            raise ValueError("start must precede end")
        if end - start > timedelta(hours=24):
            raise ValueError("duration must not exceed 24 hours")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise TypeError("timezone must be a nonempty string")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error


@dataclass(frozen=True, slots=True)
class GoogleCalendarConfig:
    calendar_id: str
    expected_service_identity: str
    oauth_scope: str
    credential_path: str
    provider_adapter_version: str
    enabled: bool

    def __post_init__(self) -> None:
        for name in (
            "calendar_id",
            "expected_service_identity",
            "oauth_scope",
            "credential_path",
            "provider_adapter_version",
        ):
            _config_text(getattr(self, name), name)
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        if self.oauth_scope != _EVENTS_SCOPE:
            raise ValueError("oauth_scope must be the calendar events scope")
        if not _VERSION.fullmatch(self.provider_adapter_version):
            raise ValueError("provider_adapter_version is invalid or exceeds its limit")


def calendar_target(config: GoogleCalendarConfig) -> TargetIdentity:
    if not isinstance(config, GoogleCalendarConfig):
        raise TypeError("config must be a GoogleCalendarConfig")
    # Bind opaque trusted provider values without exposing them as logical target IDs.
    account = "svc-" + hashlib.sha256(config.expected_service_identity.encode()).hexdigest()[:24]
    resource = "cal-" + hashlib.sha256(config.calendar_id.encode()).hexdigest()[:24]
    return TargetIdentity("google-calendar", account, resource)


__all__ = [
    "CalendarCreateRequest",
    "CanonicalCalendarEvent",
    "GoogleCalendarConfig",
    "calendar_target",
]
