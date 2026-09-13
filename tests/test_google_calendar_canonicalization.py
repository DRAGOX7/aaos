import re
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from aaos.approval.contracts import OperationId
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
    derive_provider_event_id,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
)

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
DEFAULT_START = NOW + timedelta(hours=1)
DEFAULT_END = NOW + timedelta(hours=2)


def request(
    *,
    summary: str = "Meeting",
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    timezone_name: str = "UTC",
    description: str | None = None,
    location: str | None = None,
) -> CalendarCreateRequest:
    return CalendarCreateRequest(
        summary=summary,
        start=start,
        end=end,
        timezone=timezone_name,
        description=description,
        location=location,
    )


def test_summary_limit() -> None:
    assert canonicalize_calendar_request(request(summary="x"), now=NOW).summary == "x"
    assert canonicalize_calendar_request(request(summary="Ａ" * 200), now=NOW).summary == "A" * 200

    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(summary="x" * 201), now=NOW)
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(summary="ﬃ" * 67), now=NOW)


def test_description_limit() -> None:
    assert canonicalize_calendar_request(request(description=""), now=NOW).description is None
    assert (
        canonicalize_calendar_request(request(description="x" * 2000), now=NOW).description
        == "x" * 2000
    )

    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(description="x" * 2001), now=NOW)
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(description="ﬃ" * 667), now=NOW)


def test_location_limit() -> None:
    assert canonicalize_calendar_request(request(location=""), now=NOW).location is None
    assert canonicalize_calendar_request(request(location="x" * 300), now=NOW).location == "x" * 300

    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(location="x" * 301), now=NOW)
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(location="ﬃ" * 101), now=NOW)


@pytest.mark.parametrize(
    ("field", "payload"),
    [
        pytest.param("summary", "bad\x00text", id="nul"),
        pytest.param("summary", "bad\ntext", id="unicode-cc"),
        pytest.param("summary", "invoice\u202efdp", id="unicode-cf"),
        pytest.param("description", "agenda\u2066hidden", id="description-format-control"),
        pytest.param("location", "room\u0085hidden", id="location-control"),
    ],
)
def test_nul_and_control_text_rejected(field: str, payload: str) -> None:
    if field == "summary":
        invalid_request = request(summary=payload)
    elif field == "description":
        invalid_request = request(description=payload)
    else:
        invalid_request = request(location=payload)

    with pytest.raises(ValueError):
        canonicalize_calendar_request(invalid_request, now=NOW)


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(timezone_name="No/Such"), now=NOW)


@pytest.mark.parametrize("field", ["start", "end"])
def test_floating_datetime_rejected(field: str) -> None:
    floating = datetime(2026, 1, 1, 14)
    invalid_request = request(start=floating) if field == "start" else request(end=floating)

    with pytest.raises(ValueError):
        canonicalize_calendar_request(invalid_request, now=NOW)


def test_mixed_timezone_identity_rejected() -> None:
    new_york = ZoneInfo("America/New_York")

    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            request(
                start=datetime(2026, 3, 8, 3, 30, tzinfo=new_york),
                end=datetime(2026, 3, 8, 4, 30, tzinfo=new_york),
                timezone_name="America/Chicago",
            ),
            now=NOW,
        )


@pytest.mark.parametrize("field", ["start", "end"])
def test_nonexistent_dst_local_time_rejected(field: str) -> None:
    new_york = ZoneInfo("America/New_York")
    nonexistent = datetime(2026, 3, 8, 2, 30, tzinfo=new_york)
    start = nonexistent if field == "start" else datetime(2026, 3, 8, 1, 30, tzinfo=new_york)
    end = nonexistent if field == "end" else datetime(2026, 3, 8, 3, 30, tzinfo=new_york)

    with pytest.raises(ValueError, match="nonexistent"):
        canonicalize_calendar_request(
            request(start=start, end=end, timezone_name="America/New_York"),
            now=NOW,
        )


def test_ambiguous_dst_folds_are_preserved() -> None:
    new_york = ZoneInfo("America/New_York")
    end = datetime(2026, 11, 1, 2, 30, tzinfo=new_york)

    first = canonicalize_calendar_request(
        request(
            start=datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=0),
            end=end,
            timezone_name="America/New_York",
        ),
        now=NOW,
    )
    second = canonicalize_calendar_request(
        request(
            start=datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=1),
            end=end,
            timezone_name="America/New_York",
        ),
        now=NOW,
    )

    assert first.start == "2026-11-01T05:30:00.000000Z"
    assert second.start == "2026-11-01T06:30:00.000000Z"


def test_fold_one_on_unambiguous_local_time_is_rejected() -> None:
    new_york = ZoneInfo("America/New_York")

    with pytest.raises(ValueError, match="fold"):
        canonicalize_calendar_request(
            request(
                start=datetime(2026, 11, 1, 3, 30, tzinfo=new_york, fold=1),
                end=datetime(2026, 11, 1, 4, 30, tzinfo=new_york),
                timezone_name="America/New_York",
            ),
            now=NOW,
        )


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(microseconds=-1)])
def test_start_must_precede_end(delta: timedelta) -> None:
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(end=DEFAULT_START + delta), now=NOW)


def test_duration_must_not_exceed_24_hours() -> None:
    exact = canonicalize_calendar_request(request(end=DEFAULT_START + timedelta(hours=24)), now=NOW)
    assert exact.end == "2026-01-02T13:00:00.000000Z"

    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            request(end=DEFAULT_START + timedelta(hours=24, microseconds=1)), now=NOW
        )


def test_past_start_rejected() -> None:
    assert (
        canonicalize_calendar_request(
            request(start=NOW, end=NOW + timedelta(hours=1)), now=NOW
        ).start
        == "2026-01-01T12:00:00.000000Z"
    )

    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            request(start=NOW - timedelta(microseconds=1), end=NOW + timedelta(hours=1)),
            now=NOW,
        )


def test_horizon_must_not_exceed_365_days() -> None:
    at_horizon = NOW + timedelta(days=365)
    assert (
        canonicalize_calendar_request(
            request(start=at_horizon, end=at_horizon + timedelta(hours=1)), now=NOW
        ).start
        == "2027-01-01T12:00:00.000000Z"
    )

    beyond_horizon = at_horizon + timedelta(microseconds=1)
    with pytest.raises(ValueError):
        canonicalize_calendar_request(
            request(start=beyond_horizon, end=beyond_horizon + timedelta(hours=1)), now=NOW
        )


def test_canonical_rfc3339_is_stable() -> None:
    new_york = ZoneInfo("America/New_York")
    event = canonicalize_calendar_request(
        request(
            summary="Ｍüünchen",
            start=datetime(2026, 9, 14, 9, 0, 0, 123, tzinfo=new_york),
            end=datetime(2026, 9, 14, 10, 15, 0, 456, tzinfo=new_york),
            timezone_name="America/New_York",
            description="Ångström",
            location="Room 1",
        ),
        now=NOW,
    )

    assert event == CanonicalCalendarEvent._create(
        summary="Müünchen",
        start="2026-09-14T13:00:00.000123Z",
        end="2026-09-14T14:15:00.000456Z",
        timezone="America/New_York",
        description="Ångström",
        location="Room 1",
    )


def test_absent_optionals_have_one_representation() -> None:
    absent = canonicalize_calendar_request(request(), now=NOW)
    empty = canonicalize_calendar_request(request(description="", location=""), now=NOW)

    expected = {
        "description": None,
        "end": "2026-01-01T14:00:00.000000Z",
        "location": None,
        "start": "2026-01-01T13:00:00.000000Z",
        "summary": "Meeting",
        "timezone": "UTC",
    }
    assert canonical_event_arguments(absent) == expected
    assert canonical_event_arguments(empty) == expected
    assert list(canonical_event_arguments(absent)) == [
        "description",
        "end",
        "location",
        "start",
        "summary",
        "timezone",
    ]


def test_canonical_event_arguments_rejects_forged_instance() -> None:
    forged = object.__new__(CanonicalCalendarEvent)
    for field, value in {
        "summary": "Meeting",
        "start": "bad",
        "end": "also-bad",
        "timezone": "No/Such",
        "description": None,
        "location": None,
    }.items():
        object.__setattr__(forged, field, value)

    with pytest.raises(ValueError):
        canonical_event_arguments(forged)


def test_provider_id_is_deterministic() -> None:
    first = derive_provider_event_id(OperationId("op"), derivation_version="v1")

    assert first == derive_provider_event_id(OperationId("op"), derivation_version="v1")
    assert first == "aaosbaodnvhc901eofk2c55u3efkmr9mq9846fljtoirrbp3ss0e2q"
    assert re.fullmatch(r"[0-9a-v]{5,1024}", first)


def test_different_operation_ids_produce_different_provider_ids() -> None:
    original = derive_provider_event_id(OperationId("op"), derivation_version="v1")

    assert derive_provider_event_id(OperationId("op2"), derivation_version="v1") != original
    assert derive_provider_event_id(OperationId("op"), derivation_version="v2") != original


@pytest.mark.parametrize("version", ["", "_v1", "v 1", "v" * 129])
def test_provider_id_requires_safe_bounded_derivation_version(version: str) -> None:
    with pytest.raises(ValueError):
        derive_provider_event_id(OperationId("op"), derivation_version=version)


def test_canonicalization_requires_typed_inputs() -> None:
    with pytest.raises(TypeError):
        canonicalize_calendar_request(cast(Any, {}), now=NOW)
    with pytest.raises(ValueError):
        canonicalize_calendar_request(request(), now=datetime(2026, 1, 1, 12))
