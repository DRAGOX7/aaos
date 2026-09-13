# mypy: disable-error-code=no-untyped-def,no-untyped-call
from datetime import datetime, timedelta, timezone

import pytest

from aaos.approval.contracts import OperationId
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
    derive_provider_event_id,
)
from aaos.integrations.google_calendar.contracts import CalendarCreateRequest

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def req(**kw: object) -> CalendarCreateRequest:
    return CalendarCreateRequest(
        "Meeting", NOW + timedelta(hours=1), NOW + timedelta(hours=2), "UTC", **kw
    )


def test_canonical_rfc3339_is_stable():
    assert canonicalize_calendar_request(req(), now=NOW).start.endswith("Z")


def test_absent_optionals_have_one_representation():
    assert (
        canonical_event_arguments(canonicalize_calendar_request(req(), now=NOW))["description"]
        is None
    )


def test_summary_limit():
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest(
                "x" * 201, NOW + timedelta(hours=1), NOW + timedelta(hours=2), "UTC"
            ),
            now=NOW,
        )


def test_provider_id_is_deterministic():
    assert derive_provider_event_id(
        OperationId("op"), derivation_version="v1"
    ) == derive_provider_event_id(OperationId("op"), derivation_version="v1")


def test_different_operation_ids_produce_different_provider_ids():
    assert derive_provider_event_id(
        OperationId("op"), derivation_version="v1"
    ) != derive_provider_event_id(OperationId("op2"), derivation_version="v1")


def test_description_limit() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(req(description="x" * 2001), now=NOW)


def test_location_limit() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(req(location="x" * 301), now=NOW)


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest(
                "x", NOW + timedelta(hours=1), NOW + timedelta(hours=2), "No/Such"
            ),
            now=NOW,
        )


def test_floating_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest("x", datetime(2026, 9, 14, 13), datetime(2026, 9, 14, 14), "UTC"),
            now=NOW,
        )


def test_start_must_precede_end() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest("x", NOW + timedelta(hours=2), NOW + timedelta(hours=1), "UTC"),
            now=NOW,
        )


def test_duration_must_not_exceed_24_hours() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest("x", NOW + timedelta(hours=1), NOW + timedelta(hours=26), "UTC"),
            now=NOW,
        )


def test_past_start_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest("x", NOW - timedelta(hours=1), NOW + timedelta(hours=1), "UTC"),
            now=NOW,
        )


def test_horizon_must_not_exceed_365_days() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            CalendarCreateRequest(
                "x", NOW + timedelta(days=366), NOW + timedelta(days=366, hours=1), "UTC"
            ),
            now=NOW,
        )
