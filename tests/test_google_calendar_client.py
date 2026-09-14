from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from dataclasses import fields
from typing import Any, cast

import pytest

from aaos.integrations.google_calendar.client import (
    FakeGoogleCalendarClient,
    GoogleCalendarClient,
    project_observed_event,
)
from aaos.integrations.google_calendar.contracts import CanonicalCalendarEvent
from aaos.integrations.google_calendar.errors import (
    GoogleProviderError,
    GoogleProviderErrorKind,
)

CALENDAR_ID = "team-calendar@example.com"
PROVIDER_EVENT_ID = "aaosbaodnvhc901eofk2c55u3efkmr9mq9846fljtoirrbp3ss0e2q"
OBSERVED_FIELDS = {
    "provider_event_id",
    "summary",
    "start",
    "end",
    "timezone",
    "description",
    "location",
}


def canonical_event() -> CanonicalCalendarEvent:
    return CanonicalCalendarEvent._create(
        summary="Planning meeting",
        start="2026-09-14T13:00:00.000000Z",
        end="2026-09-14T14:00:00.000000Z",
        timezone="UTC",
        description="Agenda",
        location="Room 1",
    )


def raw_event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "provider_event_id": PROVIDER_EVENT_ID,
        "summary": "Planning meeting",
        "start": "2026-09-14T13:00:00.000000Z",
        "end": "2026-09-14T14:00:00.000000Z",
        "timezone": "UTC",
        "description": "Agenda",
        "location": "Room 1",
    }
    value.update(overrides)
    return value


def assert_provider_error(
    client: FakeGoogleCalendarClient,
    expected: GoogleProviderErrorKind,
    *,
    operation: str,
) -> None:
    with pytest.raises(GoogleProviderError) as captured:
        if operation == "get":
            client.get_event(CALENDAR_ID, PROVIDER_EVENT_ID)
        else:
            client.insert_event(CALENDAR_ID, canonical_event(), PROVIDER_EVENT_ID)

    assert captured.value.kind is expected
    assert captured.value.safe_reason
    assert len(captured.value.safe_reason) <= 256


def test_get_absent_returns_none() -> None:
    client = FakeGoogleCalendarClient()

    assert client.get_event(CALENDAR_ID, PROVIDER_EVENT_ID) is None
    assert client.get_count == 1
    assert client.insert_count == 0
    assert client.events == ()


def test_get_exact_event_returns_projection() -> None:
    projected = project_observed_event(raw_event())
    client = FakeGoogleCalendarClient(initial_events=((CALENDAR_ID, projected),))

    assert client.get_event(CALENDAR_ID, PROVIDER_EVENT_ID) == projected
    assert client.get_event("another-calendar@example.com", PROVIDER_EVENT_ID) is None
    assert client.get_count == 2


def test_insert_success() -> None:
    client = FakeGoogleCalendarClient()

    result = client.insert_event(CALENDAR_ID, canonical_event(), PROVIDER_EVENT_ID)

    assert result == project_observed_event(raw_event())
    assert client.insert_count == 1
    assert client.get_count == 0
    assert client.events == (result,)


def test_duplicate_provider_id_is_closed_error() -> None:
    client = FakeGoogleCalendarClient()
    client.insert_event(CALENDAR_ID, canonical_event(), PROVIDER_EVENT_ID)

    assert_provider_error(client, GoogleProviderErrorKind.ALREADY_EXISTS, operation="insert")
    assert client.insert_count == 2
    assert len(client.events) == 1


@pytest.mark.parametrize(
    "kind",
    [
        GoogleProviderErrorKind.AUTHENTICATION_REJECTED,
        GoogleProviderErrorKind.PERMISSION_REJECTED,
        GoogleProviderErrorKind.INVALID_REQUEST,
        GoogleProviderErrorKind.NOT_FOUND,
        GoogleProviderErrorKind.RATE_LIMITED,
        GoogleProviderErrorKind.SERVER_ERROR,
        GoogleProviderErrorKind.TIMEOUT,
        GoogleProviderErrorKind.TRANSPORT_ERROR,
        GoogleProviderErrorKind.MALFORMED_RESPONSE,
    ],
)
def test_get_failure_modes_are_typed(kind: GoogleProviderErrorKind) -> None:
    assert_provider_error(FakeGoogleCalendarClient(get_error=kind), kind, operation="get")


@pytest.mark.parametrize(
    "kind",
    [
        GoogleProviderErrorKind.AUTHENTICATION_REJECTED,
        GoogleProviderErrorKind.PERMISSION_REJECTED,
        GoogleProviderErrorKind.INVALID_REQUEST,
        GoogleProviderErrorKind.NOT_FOUND,
        GoogleProviderErrorKind.RATE_LIMITED,
        GoogleProviderErrorKind.SERVER_ERROR,
        GoogleProviderErrorKind.TIMEOUT,
        GoogleProviderErrorKind.TRANSPORT_ERROR,
        GoogleProviderErrorKind.MALFORMED_RESPONSE,
    ],
)
def test_insert_failure_modes_are_typed(kind: GoogleProviderErrorKind) -> None:
    client = FakeGoogleCalendarClient(insert_error=kind)

    assert_provider_error(client, kind, operation="insert")
    assert client.events == ()


def test_timeout_is_typed() -> None:
    assert_provider_error(
        FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TIMEOUT),
        GoogleProviderErrorKind.TIMEOUT,
        operation="insert",
    )


def test_transport_error_is_typed() -> None:
    assert_provider_error(
        FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TRANSPORT_ERROR),
        GoogleProviderErrorKind.TRANSPORT_ERROR,
        operation="insert",
    )


def test_permission_rejection_is_typed() -> None:
    assert_provider_error(
        FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.PERMISSION_REJECTED),
        GoogleProviderErrorKind.PERMISSION_REJECTED,
        operation="insert",
    )


def test_invalid_request_is_typed() -> None:
    assert_provider_error(
        FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.INVALID_REQUEST),
        GoogleProviderErrorKind.INVALID_REQUEST,
        operation="insert",
    )


def test_server_failure_is_typed() -> None:
    assert_provider_error(
        FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.SERVER_ERROR),
        GoogleProviderErrorKind.SERVER_ERROR,
        operation="insert",
    )


def test_malformed_response_is_typed() -> None:
    with pytest.raises(GoogleProviderError) as captured:
        project_observed_event({"provider_event_id": PROVIDER_EVENT_ID})

    assert captured.value.kind is GoogleProviderErrorKind.MALFORMED_RESPONSE
    assert (
        captured.value.safe_reason
        == "provider response did not match the approved event projection"
    )


class HostileMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError("provider secret and transport details")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("provider secret and transport details")

    def __len__(self) -> int:
        raise RuntimeError("provider secret and transport details")


def test_hostile_provider_mapping_is_closed_without_exception_leakage() -> None:
    with pytest.raises(GoogleProviderError) as captured:
        project_observed_event(HostileMapping())

    error = captured.value
    assert error.kind is GoogleProviderErrorKind.MALFORMED_RESPONSE
    assert error.safe_reason == "provider response did not match the approved event projection"
    assert "provider secret" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_observed_projection_has_only_approved_fields() -> None:
    observed = project_observed_event(raw_event())

    assert {field.name for field in fields(observed)} == OBSERVED_FIELDS
    assert not hasattr(observed, "raw")
    assert not hasattr(observed, "etag")
    assert not hasattr(observed, "creator")


@pytest.mark.parametrize(
    "extra",
    [
        {"etag": "secret-version"},
        {"creator": {"email": "private@example.com"}},
        {"organizer": {"email": "private@example.com"}},
        {"htmlLink": "https://calendar.example/private"},
        {"attendees": [{"email": "private@example.com"}]},
        {"conferenceData": {"entryPoints": []}},
        {"reminders": {"useDefault": True}},
        {"recurrence": ["RRULE:FREQ=DAILY"]},
    ],
)
def test_projection_rejects_unknown_provider_fields(extra: dict[str, object]) -> None:
    with pytest.raises(GoogleProviderError) as captured:
        project_observed_event(raw_event(**extra))

    assert captured.value.kind is GoogleProviderErrorKind.MALFORMED_RESPONSE
    assert repr(extra) not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "x" * 201),
        ("description", "x" * 2001),
        ("location", "x" * 301),
        ("start", "not-an-instant"),
        ("end", "not-an-instant"),
        ("timezone", "No/Such"),
        ("provider_event_id", "../../secret"),
    ],
)
def test_projection_rejects_unbounded_or_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(GoogleProviderError) as captured:
        project_observed_event(raw_event(**{field: value}))

    assert captured.value.kind is GoogleProviderErrorKind.MALFORMED_RESPONSE


def test_projection_accepts_absent_optional_fields() -> None:
    raw = raw_event()
    del raw["description"]
    del raw["location"]

    observed = project_observed_event(raw)

    assert observed.description is None
    assert observed.location is None


def test_raw_provider_response_is_not_exposed() -> None:
    raw = raw_event()
    observed = project_observed_event(raw)
    client = FakeGoogleCalendarClient(initial_events=((CALENDAR_ID, observed),))
    raw["summary"] = "mutated outside the client"
    raw["credential"] = "secret"

    stored = client.get_event(CALENDAR_ID, PROVIDER_EVENT_ID)

    assert stored is not None
    assert stored.summary == "Planning meeting"
    assert "secret" not in repr(stored)
    assert "credential" not in repr(client.events)


def test_client_has_no_generic_request_surface() -> None:
    public_protocol_callables = {
        name
        for name, value in GoogleCalendarClient.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
    client = FakeGoogleCalendarClient()

    assert public_protocol_callables == {"get_event", "insert_event"}
    for name in (
        "request",
        "execute",
        "execute_api",
        "call",
        "service",
        "raw_service",
        "update_event",
        "delete_event",
        "create_calendar",
        "update_acl",
        "list_calendars",
        "batch",
    ):
        assert not hasattr(client, name)


def test_unknown_failure_modes_are_rejected() -> None:
    with pytest.raises(TypeError):
        FakeGoogleCalendarClient(get_error=cast(Any, "timeout"))
    with pytest.raises(TypeError):
        FakeGoogleCalendarClient(insert_error=cast(Any, object()))


@pytest.mark.parametrize(
    "reason",
    ["", "x" * 257, "unsafe\ntrace", "unsafe\u202esecret", "unsafe\ud800secret"],
)
def test_provider_error_reason_is_bounded_safe_text(reason: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        GoogleProviderError(GoogleProviderErrorKind.TRANSPORT_ERROR, reason)


def test_provider_error_contract_rejects_untrusted_shapes() -> None:
    with pytest.raises(TypeError):
        GoogleProviderError(cast(Any, "TRANSPORT_ERROR"), "safe")
    with pytest.raises(TypeError):
        cast(Any, GoogleProviderError)(
            GoogleProviderErrorKind.TRANSPORT_ERROR,
            "safe",
            raw_response={"token": "secret"},
        )


def test_client_module_has_no_network_credential_or_sdk_authority() -> None:
    module = inspect.getmodule(FakeGoogleCalendarClient)
    assert module is not None
    source = inspect.getsource(module)
    forbidden = (
        "googleapiclient",
        "google.auth",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "credential",
        "oauth",
        "token",
        "os.environ",
        "getenv",
    )

    assert all(term not in source for term in forbidden)
