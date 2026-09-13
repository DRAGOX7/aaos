from datetime import datetime, timezone
import pytest
from aaos.integrations.google_calendar.contracts import CalendarCreateRequest


def test_valid_minimal_request() -> None:
    value = CalendarCreateRequest(
        "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC"
    )
    assert value.description is None


def test_valid_complete_request() -> None:
    assert (
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", "d", "l"
        ).location
        == "l"
    )


@pytest.mark.parametrize(
    "kwargs", [{"calendar_id": "x"}, {"provider_event_id": "x"}, {"attendees": []}]
)
def test_unknown_fields_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", **kwargs
        )


def test_forbidden_provider_fields_are_not_representable() -> None:
    assert set(CalendarCreateRequest.__dataclass_fields__) == {
        "summary",
        "start",
        "end",
        "timezone",
        "description",
        "location",
    }


def test_user_cannot_choose_provider_event_id() -> None:
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x",
            datetime.now(timezone.utc),
            datetime.now(timezone.utc),
            "UTC",
            provider_event_id="x",
        )


def test_user_cannot_choose_calendar_id() -> None:
    with pytest.raises(TypeError):
        CalendarCreateRequest(
            "x", datetime.now(timezone.utc), datetime.now(timezone.utc), "UTC", calendar_id="x"
        )
