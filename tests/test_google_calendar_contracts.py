from datetime import datetime, timezone
import pytest
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    GoogleCalendarConfig,
    calendar_target,
)


def test_valid_minimal_request():
    value = CalendarCreateRequest(
        "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC"
    )
    assert value.description is None


def test_valid_complete_request():
    assert (
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", "d", "l"
        ).location
        == "l"
    )


@pytest.mark.parametrize(
    "kwargs", [{"calendar_id": "x"}, {"provider_event_id": "x"}, {"attendees": []}]
)
def test_unknown_fields_are_rejected(kwargs):
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", **kwargs
        )


def test_forbidden_provider_fields_are_not_representable():
    assert set(CalendarCreateRequest.__dataclass_fields__) == {
        "summary",
        "start",
        "end",
        "timezone",
        "description",
        "location",
    }


def test_user_cannot_choose_provider_event_id():
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            "UTC",
            provider_event_id="x",
        )


def test_user_cannot_choose_calendar_id():
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", calendar_id="x"
        )
