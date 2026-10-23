"""Regression: a study-task Calendar time survives the real canonicalizer (fold defect).

``_parse_local_start`` tried fold=0 and fold=1 and kept the last candidate per UTC instant,
so an ordinary time got fold=1 and the Calendar canonicalizer refused it ("start fold is
invalid for declared timezone"). Every study-task proposal failed. The earlier agent tests
used a fake proposal service that never ran the canonicalizer.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aaos.agent.calendar_actions import CalendarIntentError, build_calendar_event_plan
from aaos.integrations.google_calendar.canonicalization import canonicalize_calendar_request
from aaos.study_tasks.contracts import StudyTask, StudyTaskPriority, StudyTaskStatus

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)


def _task() -> StudyTask:
    return StudyTask(
        9,
        "Review k-fold cross-validation",
        StudyTaskStatus.OPEN,
        list(StudyTaskPriority)[0],
        None,
        None,
        None,
        None,
        None,
        NOW,
        NOW,
    )


def _plan(question: str, zone: str, now: datetime = NOW):  # type: ignore[no-untyped-def]
    return build_calendar_event_plan(
        question, assignment=None, study_task=_task(), timezone_name=zone, now=now
    )


@pytest.mark.parametrize(
    ("question", "zone"),
    [
        ("Put my study task on my calendar tomorrow at 6 pm", "Asia/Amman"),
        ("Put my study task on my calendar on 2026-10-30 at 9 am", "Asia/Amman"),
        ("Put my study task on my calendar on 2026-11-02 at 6 pm", "Europe/Berlin"),
        ("Put my study task on my calendar tomorrow at 7:30 pm", "UTC"),
    ],
)
def test_an_ordinary_time_has_fold_0_and_passes_the_real_canonicalizer(
    question: str, zone: str
) -> None:
    plan = _plan(question, zone)
    assert plan.request.start.fold == 0
    canonical = canonicalize_calendar_request(plan.request, now=NOW)  # must not raise
    assert canonical.timezone == zone


def test_a_repeated_wall_time_is_still_refused_as_ambiguous() -> None:
    # Europe/Berlin falls back on 2026-10-25 at 03:00, so 02:30 happens twice.
    with pytest.raises(CalendarIntentError):
        _plan("Put my study task on my calendar on 2026-10-25 at 2:30 am", "Europe/Berlin")


def test_a_skipped_wall_time_is_still_refused_as_nonexistent() -> None:
    # Europe/Berlin springs forward on 2027-03-28 at 02:00, so 02:30 never happens.
    with pytest.raises(CalendarIntentError):
        _plan("Put my study task on my calendar on 2027-03-28 at 2:30 am", "Europe/Berlin")
