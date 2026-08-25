from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aaos.academic.normalization import NormalizationError, normalize_collection
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
)
from aaos.moodle.integration import build_isolated_database, sync_moodle_snapshot
from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)


def _semantic_snapshot() -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system=" Moodle:Fixture:Semantic ",
        courses=(
            RawCourse(
                source_id=" course-1 ",
                title=" Synthetic Course ",
                code=" CS-101 ",
                description=" Course description ",
                source_updated_at="2026-08-25T19:00:00+03:00",
            ),
        ),
        assignments=(
            RawAssignment(
                source_id=" assignment-1 ",
                course_source_id=" course-1 ",
                course_source_system="MOODLE:FIXTURE:SEMANTIC",
                title=" Synthetic Assignment ",
                description=" Assignment description ",
                due_at="2026-08-26T01:30:00+03:00",
                source_updated_at="2026-08-25T19:05:00+03:00",
            ),
        ),
        announcements=(
            RawAnnouncement(
                source_id=" announcement-1 ",
                course_source_id=" course-1 ",
                course_source_system="moodle:fixture:semantic",
                title=" Synthetic Announcement ",
                body=" Announcement body ",
                published_at="2026-08-25T20:15:00+03:00",
                source_updated_at="2026-08-25T20:20:00+03:00",
            ),
        ),
        remote_files=(
            RawRemoteFile(
                source_id=" file-1 ",
                course_source_id=" course-1 ",
                course_source_system="Moodle:Fixture:Semantic",
                title=" Synthetic File ",
                remote_url="https://example.com/course/file.pdf?download=0",
                source_updated_at="2026-08-25T19:10:00+03:00",
            ),
        ),
    )


def _entity_counts(session: Session) -> tuple[int, int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(CourseORM)) or 0,
        session.scalar(select(func.count()).select_from(AssignmentORM)) or 0,
        session.scalar(select(func.count()).select_from(AnnouncementORM)) or 0,
        session.scalar(select(func.count()).select_from(RemoteFileORM)) or 0,
    )


def test_complete_snapshot_normalizes_and_persists_exact_semantics(
    tmp_path: Path,
) -> None:
    snapshot = _semantic_snapshot()

    canonical = normalize_collection(snapshot)

    assert canonical.source_system == "moodle:fixture:semantic"

    course = canonical.courses[0]
    assert course.source_id == "course-1"
    assert course.title == "Synthetic Course"
    assert course.code == "CS-101"
    assert course.description == "Course description"
    assert course.source_updated_at == datetime(
        2026, 8, 25, 16, 0, tzinfo=timezone.utc
    )

    assignment = canonical.assignments[0]
    assert assignment.source_id == "assignment-1"
    assert assignment.course_source_id == "course-1"
    assert assignment.title == "Synthetic Assignment"
    assert assignment.description == "Assignment description"
    assert assignment.due_at == datetime(
        2026, 8, 25, 22, 30, tzinfo=timezone.utc
    )
    assert assignment.source_updated_at == datetime(
        2026, 8, 25, 16, 5, tzinfo=timezone.utc
    )

    announcement = canonical.announcements[0]
    assert announcement.source_id == "announcement-1"
    assert announcement.course_source_id == "course-1"
    assert announcement.title == "Synthetic Announcement"
    assert announcement.body == "Announcement body"
    assert announcement.published_at == datetime(
        2026, 8, 25, 17, 15, tzinfo=timezone.utc
    )
    assert announcement.source_updated_at == datetime(
        2026, 8, 25, 17, 20, tzinfo=timezone.utc
    )

    remote_file = canonical.remote_files[0]
    assert remote_file.source_id == "file-1"
    assert remote_file.course_source_id == "course-1"
    assert remote_file.title == "Synthetic File"
    assert remote_file.remote_url == "https://example.com/course/file.pdf?download=0"
    assert remote_file.source_updated_at == datetime(
        2026, 8, 25, 16, 10, tzinfo=timezone.utc
    )

    database_path = tmp_path / "moodle-semantic-acceptance.sqlite3"
    engine, session_factory = build_isolated_database(database_path)

    try:
        sync_moodle_snapshot(snapshot, session_factory=session_factory)

        with session_factory() as session:
            persisted_course = session.scalar(
                select(CourseORM).where(
                    CourseORM.source_system == "moodle:fixture:semantic",
                    CourseORM.source_id == "course-1",
                )
            )
            persisted_assignment = session.scalar(
                select(AssignmentORM).where(
                    AssignmentORM.source_system == "moodle:fixture:semantic",
                    AssignmentORM.source_id == "assignment-1",
                )
            )
            persisted_announcement = session.scalar(
                select(AnnouncementORM).where(
                    AnnouncementORM.source_system == "moodle:fixture:semantic",
                    AnnouncementORM.source_id == "announcement-1",
                )
            )
            persisted_remote_file = session.scalar(
                select(RemoteFileORM).where(
                    RemoteFileORM.source_system == "moodle:fixture:semantic",
                    RemoteFileORM.source_id == "file-1",
                )
            )

            assert persisted_course is not None
            assert persisted_assignment is not None
            assert persisted_announcement is not None
            assert persisted_remote_file is not None

            assert persisted_course.title == "Synthetic Course"
            assert persisted_course.code == "CS-101"
            assert persisted_course.description == "Course description"
            assert persisted_course.source_updated_at == datetime(
                2026, 8, 25, 16, 0, tzinfo=timezone.utc
            )

            assert persisted_assignment.course_id == persisted_course.id
            assert persisted_assignment.title == "Synthetic Assignment"
            assert persisted_assignment.description == "Assignment description"
            assert persisted_assignment.due_at == datetime(
                2026, 8, 25, 22, 30, tzinfo=timezone.utc
            )

            assert persisted_announcement.course_id == persisted_course.id
            assert persisted_announcement.title == "Synthetic Announcement"
            assert persisted_announcement.body == "Announcement body"
            assert persisted_announcement.published_at == datetime(
                2026, 8, 25, 17, 15, tzinfo=timezone.utc
            )

            assert persisted_remote_file.course_id == persisted_course.id
            assert persisted_remote_file.title == "Synthetic File"
            assert (
                persisted_remote_file.remote_url
                == "https://example.com/course/file.pdf?download=0"
            )

            first_ids = (
                persisted_course.id,
                persisted_assignment.id,
                persisted_announcement.id,
                persisted_remote_file.id,
            )
            assert _entity_counts(session) == (1, 1, 1, 1)

        sync_moodle_snapshot(snapshot, session_factory=session_factory)

        with session_factory() as session:
            second_course = session.scalar(select(CourseORM))
            second_assignment = session.scalar(select(AssignmentORM))
            second_announcement = session.scalar(select(AnnouncementORM))
            second_remote_file = session.scalar(select(RemoteFileORM))

            assert second_course is not None
            assert second_assignment is not None
            assert second_announcement is not None
            assert second_remote_file is not None

            assert (
                second_course.id,
                second_assignment.id,
                second_announcement.id,
                second_remote_file.id,
            ) == first_ids
            assert _entity_counts(session) == (1, 1, 1, 1)
    finally:
        engine.dispose()
        if database_path.exists():
            database_path.unlink()


def test_normalization_failure_occurs_before_database_session_is_opened() -> None:
    session_opened = False

    def forbidden_session_factory() -> Session:
        nonlocal session_opened
        session_opened = True
        raise AssertionError("database session must not open before normalization succeeds")

    invalid_snapshot = CompleteSourceSnapshot(
        source_system="moodle:fixture:invalid-parent",
        courses=(RawCourse(source_id="course-1", title="Course"),),
        assignments=(
            RawAssignment(
                source_id="assignment-1",
                course_source_id="missing-course",
                title="Assignment",
            ),
        ),
    )

    with pytest.raises(NormalizationError):
        sync_moodle_snapshot(
            invalid_snapshot,
            session_factory=forbidden_session_factory,
        )

    assert session_opened is False
