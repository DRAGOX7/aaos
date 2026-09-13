from datetime import datetime, timezone
from typing import Any, cast

import pytest

from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
    calendar_target,
)

REQUEST_FIELDS = {
    "summary",
    "start",
    "end",
    "timezone",
    "description",
    "location",
}
CONFIG_FIELDS = {
    "calendar_id",
    "expected_service_identity",
    "oauth_scope",
    "credential_path",
    "provider_adapter_version",
    "enabled",
}
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
START = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
END = datetime(2026, 9, 14, 14, tzinfo=timezone.utc)


def config(**overrides: object) -> GoogleCalendarConfig:
    values: dict[str, object] = {
        "calendar_id": "team-calendar@example.com",
        "expected_service_identity": "svc@example.com",
        "oauth_scope": EVENTS_SCOPE,
        "credential_path": "C:/secure/google-calendar.json",
        "provider_adapter_version": "v1",
        "enabled": True,
    }
    values.update(overrides)
    result: object = cast(Any, GoogleCalendarConfig)(**values)
    assert isinstance(result, GoogleCalendarConfig)
    return result


def test_valid_minimal_request() -> None:
    value = CalendarCreateRequest("Meeting", START, END, "UTC")

    assert value == CalendarCreateRequest(
        summary="Meeting",
        start=START,
        end=END,
        timezone="UTC",
        description=None,
        location=None,
    )


def test_valid_complete_request() -> None:
    value = CalendarCreateRequest("Meeting", START, END, "UTC", "Agenda", "Room 1")

    assert value.description == "Agenda"
    assert value.location == "Room 1"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"calendar_id": "other@example.com"}, id="calendar-id"),
        pytest.param({"provider_event_id": "chosen-by-user"}, id="provider-event-id"),
        pytest.param({"attendees": []}, id="attendees"),
        pytest.param({"recurrence": ["RRULE:FREQ=DAILY"]}, id="recurrence"),
        pytest.param({"reminders": {"useDefault": True}}, id="reminders"),
        pytest.param({"conferenceData": {"createRequest": {}}}, id="conference-data"),
        pytest.param({"provider_fields": {"sendUpdates": "all"}}, id="arbitrary-mapping"),
    ],
)
def test_unknown_fields_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        cast(Any, CalendarCreateRequest)("Meeting", START, END, "UTC", **kwargs)


def test_forbidden_provider_fields_are_not_representable() -> None:
    assert set(CalendarCreateRequest.__dataclass_fields__) == REQUEST_FIELDS
    assert set(CanonicalCalendarEvent.__dataclass_fields__) == REQUEST_FIELDS
    assert set(GoogleCalendarConfig.__dataclass_fields__) == CONFIG_FIELDS


def test_user_cannot_choose_provider_event_id() -> None:
    with pytest.raises(TypeError):
        cast(Any, CalendarCreateRequest)(
            "Meeting", START, END, "UTC", provider_event_id="chosen-by-user"
        )


def test_user_cannot_choose_calendar_id() -> None:
    with pytest.raises(TypeError):
        cast(Any, CalendarCreateRequest)(
            "Meeting", START, END, "UTC", calendar_id="other@example.com"
        )


def test_canonical_event_rejects_direct_construction() -> None:
    with pytest.raises(TypeError):
        cast(Any, CanonicalCalendarEvent)()
    with pytest.raises(TypeError):
        cast(Any, CanonicalCalendarEvent)(
            "Meeting",
            "2026-09-14T13:00:00.000000Z",
            "2026-09-14T14:00:00.000000Z",
            "UTC",
            None,
            None,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"summary": "Meeting\u202e"}, id="format-control"),
        pytest.param({"summary": "Meeting\ud800"}, id="surrogate"),
        pytest.param({"description": "Agenda\ud800"}, id="description-surrogate"),
        pytest.param({"location": "Room\ud800"}, id="location-surrogate"),
        pytest.param({"summary": "Å"}, id="noncanonical-unicode"),
        pytest.param({"start": "not-rfc3339"}, id="malformed-start"),
        pytest.param({"end": "2026-09-14T12:00:00.000000Z"}, id="reversed-instants"),
        pytest.param({"timezone": "No/Such"}, id="invalid-timezone"),
        pytest.param({"description": ""}, id="noncanonical-empty-description"),
        pytest.param({"location": ""}, id="noncanonical-empty-location"),
    ],
)
def test_canonical_event_rejects_forged_factory_values(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "summary": "Meeting",
        "start": "2026-09-14T13:00:00.000000Z",
        "end": "2026-09-14T14:00:00.000000Z",
        "timezone": "UTC",
        "description": None,
        "location": None,
    }
    values.update(overrides)

    with pytest.raises((TypeError, ValueError)):
        cast(Any, CanonicalCalendarEvent)._create(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("calendar_id", 1, id="calendar-id"),
        pytest.param("expected_service_identity", object(), id="service-identity"),
        pytest.param("oauth_scope", None, id="oauth-scope"),
        pytest.param("credential_path", [], id="credential-path"),
        pytest.param("provider_adapter_version", 1.0, id="adapter-version"),
        pytest.param("enabled", 1, id="enabled"),
    ],
)
def test_google_calendar_config_rejects_wrong_types(field: str, value: object) -> None:
    with pytest.raises(TypeError):
        config(**{field: value})


def test_google_calendar_config_requires_exact_scope() -> None:
    assert config().oauth_scope == EVENTS_SCOPE

    for scope in ("", "https://www.googleapis.com/auth/calendar", f"{EVENTS_SCOPE} "):
        with pytest.raises(ValueError):
            config(oauth_scope=scope)


@pytest.mark.parametrize(
    "field",
    ["calendar_id", "expected_service_identity", "credential_path"],
)
def test_google_calendar_config_values_are_nonempty_and_bounded(field: str) -> None:
    with pytest.raises(ValueError):
        config(**{field: ""})
    with pytest.raises(ValueError):
        config(**{field: "x" * 4097})

    assert getattr(config(**{field: "x" * 4096}), field) == "x" * 4096


def test_google_calendar_config_adapter_version_is_safe_and_bounded() -> None:
    assert (
        config(provider_adapter_version="v1.release-2").provider_adapter_version == "v1.release-2"
    )

    for version in ("", "_v1", "v 1", "v" * 129):
        with pytest.raises(ValueError):
            config(provider_adapter_version=version)


@pytest.mark.parametrize(
    "field",
    ["calendar_id", "expected_service_identity", "credential_path"],
)
def test_google_calendar_config_rejects_unicode_surrogates(field: str) -> None:
    with pytest.raises(ValueError):
        config(**{field: "trusted\ud800value"})


def test_calendar_target_is_a_trusted_deterministic_projection() -> None:
    target = calendar_target(config())

    assert target.to_dict() == {
        "provider": "google-calendar",
        "account": "svc-c45e3200c0a454b89778e8f7",
        "resource": "cal-18a0e19a676e7dd245eba554",
    }
    assert "svc@example.com" not in target.account
    assert "team-calendar@example.com" not in target.resource
    assert calendar_target(config(calendar_id="other@example.com")) != target


def test_calendar_target_accepts_only_trusted_config() -> None:
    with pytest.raises(TypeError):
        calendar_target(cast(Any, {"calendar_id": "team-calendar@example.com"}))
