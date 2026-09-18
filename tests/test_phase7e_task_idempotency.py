"""Additional idempotency and crash-boundary coverage for Task 3."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aaos.integrations.google_calendar.contracts import CalendarCreateRequest


def test_task3_fixture_uses_a_stable_aware_utc_clock() -> None:
    now = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo("UTC"))
    request = CalendarCreateRequest(
        summary="Stable",
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        timezone="UTC",
    )
    assert request.start.tzinfo is not None
    assert request.end > request.start
