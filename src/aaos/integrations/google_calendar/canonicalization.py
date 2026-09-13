from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aaos.agent.contracts import JSONValue
from aaos.approval.contracts import OperationId

from .contracts import CalendarCreateRequest, CanonicalCalendarEvent

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _text(value: str | None, name: str, limit: int, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = unicodedata.normalize("NFKC", value)
    if optional and value == "":
        return None
    if (
        not value
        or len(value) > limit
        or any(
            ord(c) < 32 or 127 <= ord(c) <= 159 or unicodedata.category(c) in {"Cc", "Cf", "Cs"}
            for c in value
        )
    ):
        raise ValueError(f"{name} is invalid or exceeds its limit")
    return value


def _required_text(value: str, name: str, limit: int) -> str:
    result = _text(value, name, limit)
    assert result is not None
    return result


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("timezone must be a valid IANA timezone") from error


def _instant(value: datetime, zone: ZoneInfo, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if getattr(value.tzinfo, "key", None) != zone.key:
        raise ValueError(f"{name} timezone must match declared timezone")
    utc_value = value.astimezone(timezone.utc)
    roundtrip = utc_value.astimezone(zone)
    if roundtrip.replace(tzinfo=None) != value.replace(tzinfo=None):
        raise ValueError(f"{name} is nonexistent in declared timezone")
    if roundtrip.utcoffset() != value.utcoffset():
        raise ValueError(f"{name} offset is invalid for declared timezone")
    if roundtrip.fold != value.fold:
        raise ValueError(f"{name} fold is invalid for declared timezone")
    return utc_value


def _rfc3339(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonicalize_calendar_request(
    request: CalendarCreateRequest, *, now: datetime
) -> CanonicalCalendarEvent:
    if not isinstance(request, CalendarCreateRequest):
        raise TypeError("request must be a CalendarCreateRequest")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    zone = _zone(request.timezone)
    start, end = _instant(request.start, zone, "start"), _instant(request.end, zone, "end")
    current = now.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("start must precede end")
    if end - start > timedelta(hours=24):
        raise ValueError("duration must not exceed 24 hours")
    if start < current:
        raise ValueError("start cannot be in the past")
    if start > current + timedelta(days=365):
        raise ValueError("start exceeds horizon")
    return CanonicalCalendarEvent._create(
        _required_text(request.summary, "summary", 200),
        _rfc3339(start),
        _rfc3339(end),
        request.timezone,
        _text(request.description, "description", 2000, True),
        _text(request.location, "location", 300, True),
    )


def canonical_event_arguments(event: CanonicalCalendarEvent) -> Mapping[str, JSONValue]:
    if not isinstance(event, CanonicalCalendarEvent):
        raise TypeError("event must be canonical")
    event._validate()
    return {
        "description": event.description,
        "end": event.end,
        "location": event.location,
        "start": event.start,
        "summary": event.summary,
        "timezone": event.timezone,
    }


def derive_provider_event_id(operation_id: OperationId, *, derivation_version: str) -> str:
    if not isinstance(operation_id, OperationId):
        raise TypeError("operation_id must be an OperationId")
    if not isinstance(derivation_version, str) or not _ID.fullmatch(derivation_version):
        raise ValueError("invalid derivation version")
    digest = hashlib.sha256(
        f"aaos/google-calendar/event/{derivation_version}/{operation_id.value}".encode()
    ).digest()
    return "aaos" + base64.b32hexencode(digest).decode().lower().rstrip("=")[:50]
