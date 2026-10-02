"""Archived (past) courses: archive transitions applied by complete syncs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from aaos.academic.normalization import normalize_collection
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
)
from aaos.academic.sync import AcademicSyncService
from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)
from aaos.persistence.database import create_database_engine, create_session_factory

SUMMER_END = datetime(2026, 10, 2, 8, 16, tzinfo=timezone.utc)
FALL_LATER = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def _snapshot(*course_ids: str, files: tuple[str, ...] = ()) -> object:
    """Each course gets one assignment and one announcement; ``files`` are (course) pairs."""
    courses = tuple(RawCourse(source_id=course, title=course.upper()) for course in course_ids)
    return normalize_collection(
        CompleteSourceSnapshot(
            source_system="mock:primary",
            courses=courses,
            assignments=tuple(
                RawAssignment(source_id=f"a-{c}", course_source_id=c, title="Task")
                for c in course_ids
            ),
            announcements=tuple(
                RawAnnouncement(source_id=f"n-{c}", course_source_id=c, title="News")
                for c in course_ids
            ),
            remote_files=tuple(
                RawRemoteFile(
                    source_id=f"f-{file}", course_source_id=file.split(":")[0], title=file
                )
                for file in files
            ),
        )
    )


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _service(tmp_path: Path) -> tuple[AcademicSyncService, object, _Clock]:
    engine = create_database_engine(tmp_path / "archive.db")
    CourseORM.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    clock = _Clock(SUMMER_END)
    return AcademicSyncService(sessions, clock=clock), sessions, clock


def _state(sessions: object, model: type, source_id: str) -> tuple[bool, datetime | None]:
    with sessions() as session:  # type: ignore[operator]
        row = session.scalar(select(model).where(model.source_id == source_id))
        return row.is_active, row.archived_at


def test_a_course_no_longer_listed_is_archived_with_its_items(tmp_path: Path) -> None:
    service, sessions, _clock = _service(tmp_path)
    service.sync(_snapshot("summer", files=("summer:1",)))

    service.sync(_snapshot("fall"))

    assert _state(sessions, CourseORM, "summer") == (False, SUMMER_END)
    assert _state(sessions, RemoteFileORM, "f-summer:1") == (False, SUMMER_END)
    assert _state(sessions, AnnouncementORM, "n-summer") == (False, SUMMER_END)
    assert _state(sessions, AssignmentORM, "a-summer") == (False, SUMMER_END)
    assert _state(sessions, CourseORM, "fall") == (True, None)


def test_an_item_removed_from_a_current_course_is_removed_not_archived(tmp_path: Path) -> None:
    service, sessions, _clock = _service(tmp_path)
    service.sync(_snapshot("fall", files=("fall:1", "fall:2")))

    service.sync(_snapshot("fall", files=("fall:1",)))

    assert _state(sessions, RemoteFileORM, "f-fall:2") == (False, None)
    assert _state(sessions, RemoteFileORM, "f-fall:1") == (True, None)


def test_an_archived_course_keeps_its_original_archive_time(tmp_path: Path) -> None:
    service, sessions, clock = _service(tmp_path)
    service.sync(_snapshot("summer", files=("summer:1",)))
    service.sync(_snapshot("fall"))

    clock.value = FALL_LATER
    service.sync(_snapshot("fall"))

    assert _state(sessions, CourseORM, "summer") == (False, SUMMER_END)
    assert _state(sessions, RemoteFileORM, "f-summer:1") == (False, SUMMER_END)


def test_a_relisted_course_becomes_current_and_unlisted_items_become_removed(
    tmp_path: Path,
) -> None:
    service, sessions, clock = _service(tmp_path)
    service.sync(_snapshot("summer", files=("summer:1", "summer:2")))
    service.sync(_snapshot("fall"))

    clock.value = FALL_LATER
    service.sync(_snapshot("summer", files=("summer:1",)))

    assert _state(sessions, CourseORM, "summer") == (True, None)
    assert _state(sessions, RemoteFileORM, "f-summer:1") == (True, None)
    assert _state(sessions, RemoteFileORM, "f-summer:2") == (False, None)


def test_migration_backfills_courses_archived_by_the_latest_complete_sync(
    tmp_path: Path,
) -> None:
    from alembic import command
    from sqlalchemy import text
    from test_document_persistence import make_config

    database_path = tmp_path / "backfill.db"
    config = make_config(database_path)
    command.upgrade(config, "0013_rag_document_summaries")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO moodle_sync_snapshots (source_system, started_at, finished_at, "
                    "status, snapshot_version, snapshot_state) VALUES ('m', "
                    "'2026-10-02T08:15:00Z', '2026-10-02T08:16:07Z', 'COMPLETE', 1, '{}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO courses (id, source_system, source_id, title, is_active) VALUES "
                    "(1, 'm', 'summer', 'Summer', 0), (2, 'm', 'fall', 'Fall', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO remote_files (source_system, source_id, course_id, title, "
                    "is_active) VALUES ('m', 'f1', 1, 'Syllabus', 0), "
                    "('m', 'f2', 2, 'Deleted', 0), ('m', 'f3', 2, 'Live', 1)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            courses = {
                row[0]: row[1]
                for row in connection.execute(text("SELECT source_id, archived_at FROM courses"))
            }
            files = {
                row[0]: row[1]
                for row in connection.execute(
                    text("SELECT source_id, archived_at FROM remote_files")
                )
            }
    finally:
        engine.dispose()
    assert courses == {"summer": "2026-10-02T08:16:07Z", "fall": None}
    assert files == {"f1": "2026-10-02T08:16:07Z", "f2": None, "f3": None}


def _archived_world(tmp_path: Path) -> tuple[object, dict[str, int]]:
    """Summer archived (file, assignment, announcement); Fall current with one file."""
    from aaos.persistence.document_models import DocumentORM

    service, sessions, _clock = _service(tmp_path)
    service.sync(_snapshot("summer", files=("summer:1",)))
    service.sync(_snapshot("fall", files=("fall:1",)))
    with sessions() as session, session.begin():  # type: ignore[operator]
        ids = {row.source_id: row.id for row in session.scalars(select(CourseORM)).all()}
        for remote in session.scalars(select(RemoteFileORM)).all():
            session.add(
                DocumentORM(
                    remote_file_id=remote.id,
                    ingestion_status="READY",
                    created_at=SUMMER_END,
                    updated_at=SUMMER_END,
                )
            )
    return sessions, ids


def test_courses_list_current_first_then_past_with_status(tmp_path: Path) -> None:
    from aaos.web.academic import AcademicReadService

    sessions, _ids = _archived_world(tmp_path)

    courses = AcademicReadService(sessions).list_courses()  # type: ignore[arg-type]

    assert [(c.title, c.status, c.archived_at) for c in courses] == [
        ("FALL", "current", None),
        ("SUMMER", "past", SUMMER_END),
    ]


def test_past_course_items_appear_only_when_the_course_is_named(tmp_path: Path) -> None:
    from aaos.web.academic import AcademicReadService
    from aaos.web.documents import DocumentReadService

    sessions, ids = _archived_world(tmp_path)
    academic = AcademicReadService(sessions)  # type: ignore[arg-type]
    documents = DocumentReadService(sessions)  # type: ignore[arg-type]

    assert {item.title for item in academic.list_announcements()} == {"News"}
    assert all(item.course_id == ids["fall"] for item in academic.list_announcements())
    assert all(item.course_id == ids["fall"] for item in academic.list_assignments())
    assert {d.course_id for d in documents.list_documents()} == {ids["fall"]}

    assert [item.course_id for item in academic.list_announcements((ids["summer"],))] == [
        ids["summer"]
    ]
    assert [item.course_id for item in academic.list_assignments(ids["summer"])] == [ids["summer"]]
    assert [d.course_id for d in documents.list_documents(ids["summer"])] == [ids["summer"]]
    assert academic.get_course(ids["summer"]).status == "past"
