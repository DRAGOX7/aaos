from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
)
from aaos.moodle.integration import build_isolated_database, sync_moodle_snapshot


def _complete_graph() -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system="moodle:fixture:isolated",
        courses=(RawCourse(source_id="course-1", title="Synthetic Course"),),
        assignments=(
            RawAssignment(
                source_id="assignment-1",
                course_source_id="course-1",
                title="Synthetic Assignment",
            ),
        ),
        announcements=(
            RawAnnouncement(
                source_id="announcement-1",
                course_source_id="course-1",
                title="Synthetic Announcement",
            ),
        ),
        remote_files=(
            RawRemoteFile(
                source_id="file-1",
                course_source_id="course-1",
                title="Synthetic File",
            ),
        ),
    )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def test_isolated_database_applies_normal_migrations_and_repeat_sync_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "moodle-acceptance.sqlite3"

    engine, session_factory = build_isolated_database(database_path)

    assert database_path.is_file()
    assert {
        "courses",
        "assignments",
        "announcements",
        "remote_files",
    }.issubset(set(inspect(engine).get_table_names()))

    snapshot = _complete_graph()
    sync_moodle_snapshot(snapshot, session_factory=session_factory)

    first_counts = {
        "courses": _count(engine, "courses"),
        "assignments": _count(engine, "assignments"),
        "announcements": _count(engine, "announcements"),
        "remote_files": _count(engine, "remote_files"),
    }
    assert first_counts == {
        "courses": 1,
        "assignments": 1,
        "announcements": 1,
        "remote_files": 1,
    }

    sync_moodle_snapshot(snapshot, session_factory=session_factory)

    second_counts = {
        "courses": _count(engine, "courses"),
        "assignments": _count(engine, "assignments"),
        "announcements": _count(engine, "announcements"),
        "remote_files": _count(engine, "remote_files"),
    }
    assert second_counts == first_counts

    engine.dispose()
    database_path.unlink()
    assert database_path.exists() is False
