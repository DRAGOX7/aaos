"""Phase 10 slice 10B: notification events from complete snapshot pairs."""

from __future__ import annotations

from datetime import datetime, timezone

from aaos.moodle.changes import (
    AnnouncementState,
    AssignmentState,
    CourseState,
    DocumentState,
    NormalizedMoodleState,
)
from aaos.notifications.events import NotificationKind, derive_events, moodle_path

SOURCE = "moodle:just:fall-2026"
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
AI = CourseState("3818", "AI380-OPTIMIZATION ALGORITHMS (All Sections )", None)
SE = CourseState("4001", "SE440-PROJECT MANAGEMENT (All Sections )", None)


def state(
    courses: tuple[CourseState, ...] = (AI,),
    assignments: tuple[AssignmentState, ...] = (),
    announcements: tuple[AnnouncementState, ...] = (),
    documents: tuple[DocumentState, ...] = (),
) -> NormalizedMoodleState:
    from aaos.moodle.changes import deserialize_state, serialize_state

    built = NormalizedMoodleState(SOURCE, courses, assignments, announcements, documents)
    return deserialize_state(serialize_state(built))


def events(before: NormalizedMoodleState, after: NormalizedMoodleState, to_id: int = 2):
    return derive_events(
        before, after, from_snapshot_id=to_id - 1, to_snapshot_id=to_id, detected_at=NOW
    )


DUE_1 = datetime(2026, 10, 12, 20, 59, tzinfo=timezone.utc)
DUE_2 = datetime(2026, 10, 15, 20, 59, tzinfo=timezone.utc)
HW = AssignmentState("assign-cm:501", "3818", "Assignment 3", DUE_1)
POST = AnnouncementState("9001", "3818", "Midterm Exam Information", None)


def test_nothing_changed_gives_no_events() -> None:
    assert events(state(), state()) == ()


def test_a_new_announcement_is_one_event_with_a_trusted_link() -> None:
    (event,) = events(state(), state(announcements=(POST,)))

    assert event.kind is NotificationKind.NEW_ANNOUNCEMENT
    assert event.payload["course"] == "AI380 · Optimization Algorithms"
    assert event.payload["title"] == "Midterm Exam Information"
    assert event.payload["path"] == "/current/mod/forum/discuss.php?d=9001"


def test_a_new_assignment_carries_its_due_date() -> None:
    (event,) = events(state(), state(assignments=(HW,)))

    assert event.kind is NotificationKind.NEW_ASSIGNMENT
    assert event.payload["due_at"] == DUE_1.isoformat()
    assert event.payload["path"] == "/current/mod/assign/view.php?id=501"


def test_a_moved_deadline_is_one_event_with_before_and_after() -> None:
    moved = AssignmentState("assign-cm:501", "3818", "Assignment 3", DUE_2)
    (event,) = events(state(assignments=(HW,)), state(assignments=(moved,)))

    assert event.kind is NotificationKind.DEADLINE_CHANGED
    assert event.payload["previous_due_at"] == DUE_1.isoformat()
    assert event.payload["due_at"] == DUE_2.isoformat()


def test_a_deadline_moved_twice_gives_two_different_keys() -> None:
    moved = AssignmentState("assign-cm:501", "3818", "Assignment 3", DUE_2)
    back = AssignmentState("assign-cm:501", "3818", "Assignment 3", DUE_1)
    (first,) = events(state(assignments=(HW,)), state(assignments=(moved,)), to_id=2)
    (second,) = events(state(assignments=(moved,)), state(assignments=(back,)), to_id=3)

    assert first.event_key != second.event_key


def test_the_same_change_seen_from_another_pair_has_the_same_key() -> None:
    (first,) = events(state(), state(announcements=(POST,)), to_id=2)
    (again,) = events(state(), state(announcements=(POST,)), to_id=7)

    assert first.event_key == again.event_key


def test_a_new_course_is_one_event() -> None:
    (event,) = events(state(), state(courses=(AI, SE)))

    assert event.kind is NotificationKind.NEW_COURSE
    assert event.payload["course"] == "SE440 · Project Management"
    assert event.payload["path"] == "/current/course/view.php?id=4001"


def test_an_archived_course_is_one_event_without_its_items() -> None:
    before = state(
        courses=(AI, SE),
        assignments=(AssignmentState("assign-cm:777", "4001", "Charter", DUE_1),),
        announcements=(AnnouncementState("8001", "4001", "Welcome", None),),
        documents=(DocumentState("resource-cm:1", "4001", "Syllabus.pdf"),),
    )
    (event,) = events(before, state(courses=(AI,)))

    assert event.kind is NotificationKind.COURSE_ARCHIVED
    assert event.payload["course"] == "SE440 · Project Management"


def test_title_edits_removals_and_files_are_not_notified_in_v1() -> None:
    renamed = AssignmentState("assign-cm:501", "3818", "Assignment 3 (updated)", DUE_1)
    doc = DocumentState("resource-cm:2", "3818", "Lecture 1.pdf")

    assert events(state(assignments=(HW,)), state(assignments=(renamed,))) == ()
    assert events(state(assignments=(HW,)), state()) == ()
    assert events(state(), state(documents=(doc,))) == ()


def test_links_are_only_built_from_numeric_ids() -> None:
    assert moodle_path("ANNOUNCEMENT", "9001") == "/current/mod/forum/discuss.php?d=9001"
    assert moodle_path("ANNOUNCEMENT", "9001?x=javascript:alert(1)") is None
    assert moodle_path("ASSIGNMENT", "assign-cm:12a") is None
    assert moodle_path("COURSE", "../../etc") is None
