from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from .contracts import CanonicalCalendarEvent
from .errors import GoogleProviderError, GoogleProviderErrorKind

_PROVIDER_EVENT_ID = re.compile(r"^[0-9a-v]{5,1024}$")
_PROJECTION_FIELDS = {
    "provider_event_id",
    "summary",
    "start",
    "end",
    "timezone",
    "description",
    "location",
}
_PROJECTION_REQUIRED_FIELDS = {
    "provider_event_id",
    "summary",
    "start",
    "end",
    "timezone",
}
_MALFORMED_RESPONSE_REASON = "provider response did not match the approved event projection"
_FAILURE_REASONS = {
    GoogleProviderErrorKind.AUTHENTICATION_REJECTED: "provider authentication was rejected",
    GoogleProviderErrorKind.PERMISSION_REJECTED: "provider permission was rejected",
    GoogleProviderErrorKind.INVALID_REQUEST: "provider rejected the request",
    GoogleProviderErrorKind.NOT_FOUND: "provider event was not found",
    GoogleProviderErrorKind.ALREADY_EXISTS: "provider event already exists",
    GoogleProviderErrorKind.RATE_LIMITED: "provider rate limit was reached",
    GoogleProviderErrorKind.SERVER_ERROR: "provider server failed",
    GoogleProviderErrorKind.TIMEOUT: "provider request timed out",
    GoogleProviderErrorKind.TRANSPORT_ERROR: "provider transport failed",
    GoogleProviderErrorKind.MALFORMED_RESPONSE: _MALFORMED_RESPONSE_REASON,
}


def _validate_provider_event_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("provider_event_id must be a string")
    if not _PROVIDER_EVENT_ID.fullmatch(value):
        raise ValueError("provider_event_id is invalid or exceeds its limit")
    return value


def _validate_calendar_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("calendar_id must be a string")
    if (
        not value
        or len(value) > 4096
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise ValueError("calendar_id is invalid or exceeds its limit")
    return value


@dataclass(frozen=True, slots=True)
class ObservedGoogleEvent:
    provider_event_id: str
    summary: str
    start: str
    end: str
    timezone: str
    description: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        _validate_provider_event_id(self.provider_event_id)
        CanonicalCalendarEvent._create(
            self.summary,
            self.start,
            self.end,
            self.timezone,
            self.description,
            self.location,
        )


class GoogleCalendarClient(Protocol):
    def get_event(self, calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None: ...

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent: ...


def _projection_value(raw: Mapping[object, object], name: str) -> object:
    if name in raw:
        return raw[name]
    return None


def project_observed_event(raw: object) -> ObservedGoogleEvent:
    """Project an untrusted provider-shaped value into the closed observation DTO."""

    try:
        if not isinstance(raw, Mapping):
            raise TypeError
        keys = set(raw)
        if not all(isinstance(key, str) for key in keys):
            raise TypeError
        if not _PROJECTION_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(_PROJECTION_FIELDS):
            raise ValueError
        provider_event_id = _projection_value(raw, "provider_event_id")
        summary = _projection_value(raw, "summary")
        start = _projection_value(raw, "start")
        end = _projection_value(raw, "end")
        timezone = _projection_value(raw, "timezone")
        description = _projection_value(raw, "description")
        location = _projection_value(raw, "location")
        if not isinstance(provider_event_id, str):
            raise TypeError
        if not isinstance(summary, str):
            raise TypeError
        if not isinstance(start, str):
            raise TypeError
        if not isinstance(end, str):
            raise TypeError
        if not isinstance(timezone, str):
            raise TypeError
        if description is not None and not isinstance(description, str):
            raise TypeError
        if location is not None and not isinstance(location, str):
            raise TypeError
        return ObservedGoogleEvent(
            provider_event_id=provider_event_id,
            summary=summary,
            start=start,
            end=end,
            timezone=timezone,
            description=description,
            location=location,
        )
    except (TypeError, ValueError):
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        ) from None


class FakeGoogleCalendarClient:
    """Deterministic bounded in-memory implementation of GoogleCalendarClient."""

    def __init__(
        self,
        *,
        initial_events: tuple[tuple[str, ObservedGoogleEvent], ...] = (),
        get_error: GoogleProviderErrorKind | None = None,
        insert_error: GoogleProviderErrorKind | None = None,
    ) -> None:
        if not isinstance(initial_events, tuple):
            raise TypeError("initial_events must be a tuple")
        if get_error is not None and not isinstance(get_error, GoogleProviderErrorKind):
            raise TypeError("get_error must be a GoogleProviderErrorKind")
        if insert_error is not None and not isinstance(insert_error, GoogleProviderErrorKind):
            raise TypeError("insert_error must be a GoogleProviderErrorKind")
        events: dict[tuple[str, str], ObservedGoogleEvent] = {}
        for seed in initial_events:
            if not isinstance(seed, tuple) or len(seed) != 2:
                raise TypeError("each initial event must be a calendar/event tuple")
            calendar_id, event = seed
            _validate_calendar_id(calendar_id)
            if not isinstance(event, ObservedGoogleEvent):
                raise TypeError("initial event must be an ObservedGoogleEvent")
            key = (calendar_id, event.provider_event_id)
            if key in events:
                raise ValueError("initial events contain a duplicate provider event ID")
            events[key] = event
        self._events = events
        self._get_error = get_error
        self._insert_error = insert_error
        self._get_count = 0
        self._insert_count = 0
        self._lock = Lock()

    @property
    def get_count(self) -> int:
        with self._lock:
            return self._get_count

    @property
    def insert_count(self) -> int:
        with self._lock:
            return self._insert_count

    @property
    def events(self) -> tuple[ObservedGoogleEvent, ...]:
        with self._lock:
            return tuple(event for _, event in sorted(self._events.items()))

    def get_event(self, calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None:
        trusted_calendar_id = _validate_calendar_id(calendar_id)
        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)
        with self._lock:
            self._get_count += 1
            if self._get_error is not None:
                raise GoogleProviderError(
                    self._get_error,
                    _FAILURE_REASONS[self._get_error],
                )
            return self._events.get((trusted_calendar_id, trusted_provider_event_id))

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        trusted_calendar_id = _validate_calendar_id(calendar_id)
        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)
        if not isinstance(event, CanonicalCalendarEvent):
            raise TypeError("event must be a CanonicalCalendarEvent")
        event._validate()
        observed = ObservedGoogleEvent(
            provider_event_id=trusted_provider_event_id,
            summary=event.summary,
            start=event.start,
            end=event.end,
            timezone=event.timezone,
            description=event.description,
            location=event.location,
        )
        key = (trusted_calendar_id, trusted_provider_event_id)
        with self._lock:
            self._insert_count += 1
            if self._insert_error is not None:
                raise GoogleProviderError(
                    self._insert_error,
                    _FAILURE_REASONS[self._insert_error],
                )
            if key in self._events:
                raise GoogleProviderError(
                    GoogleProviderErrorKind.ALREADY_EXISTS,
                    _FAILURE_REASONS[GoogleProviderErrorKind.ALREADY_EXISTS],
                )
            self._events[key] = observed
            return observed


__all__ = [
    "FakeGoogleCalendarClient",
    "GoogleCalendarClient",
    "ObservedGoogleEvent",
    "project_observed_event",
]
