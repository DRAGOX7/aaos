from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aaos.moodle.change_queries import (
    is_moodle_change_query,
    is_moodle_change_request,
    resolve_change_time_window,
)
from aaos.moodle.changes import ComparisonUnavailableReason

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "question",
    (
        "What changed in Moodle?",
        "What changed in Deep Learning since yesterday?",
        "Did the deadline change since 2026-09-20?",
        "Show anything new since last sync",
    ),
)
def test_only_change_questions_or_explicit_since_requests_enable_change_intent(
    question: str,
) -> None:
    assert is_moodle_change_request(question)


def test_query_classifier_covers_assignment_change_reads_without_action_intent() -> None:
    assert is_moodle_change_query("Which assignments changed?")
    assert is_moodle_change_query("Was there a change to the deadline?")
    action_text = "Update my study task deadline to match that change."
    assert not is_moodle_change_query(action_text)
    assert is_moodle_change_request(action_text)


def test_unrelated_academic_question_does_not_enable_change_intent() -> None:
    assert not is_moodle_change_request("List the assignments due next week")
    assert not is_moodle_change_request("List assignments since yesterday")


def test_yesterday_is_a_rolling_twenty_four_hour_utc_boundary() -> None:
    result = resolve_change_time_window("What changed since yesterday?", now=NOW)

    assert result.boundary == NOW - timedelta(hours=24)
    assert result.unavailable_reason is None


def test_iso_date_and_aware_timestamp_are_parsed_deterministically() -> None:
    date_result = resolve_change_time_window("What changed since 2026-09-20?", now=NOW)
    timestamp_result = resolve_change_time_window(
        "What changed since 2026-09-20T10:30:00+03:00?",
        now=NOW,
    )

    assert date_result.boundary == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert timestamp_result.boundary == datetime(2026, 9, 20, 7, 30, tzinfo=timezone.utc)


def test_unsupported_since_language_is_typed_unavailable() -> None:
    result = resolve_change_time_window("What changed since last week?", now=NOW)

    assert result.boundary is None
    assert result.unavailable_reason is ComparisonUnavailableReason.UNSUPPORTED_TIME_WINDOW


def test_last_sync_uses_the_default_previous_complete_snapshot_pair() -> None:
    result = resolve_change_time_window("What changed since my last successful sync?", now=NOW)

    assert result.boundary is None
    assert result.unavailable_reason is None
