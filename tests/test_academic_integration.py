from __future__ import annotations

from pathlib import Path

from aaos.academic.mock import MockAcademicSource
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


def test_complete_mock_to_normalization_to_sync_path(tmp_path: Path) -> None:
    source = MockAcademicSource(
        {
            "initial": CompleteSourceSnapshot(
                source_system="mock:primary",
                courses=(
                    RawCourse(source_id="c1", title="Math"),
                    RawCourse(source_id="c2", title="Physics"),
                ),
                assignments=(
                    RawAssignment(
                        source_id="a1",
                        course_source_id="c1",
                        title="Homework",
                        due_at="2026-08-13T12:00:00Z",
                    ),
                ),
                announcements=(
                    RawAnnouncement(source_id="n1", course_source_id="c2", title="Notice"),
                ),
                remote_files=(
                    RawRemoteFile(
                        source_id="r1",
                        course_source_id="c1",
                        title="Notes",
                        remote_url="https://example.test/notes",
                    ),
                ),
            ),
        }
    )
    engine = create_database_engine(tmp_path / "integration.db")
    CourseORM.metadata.create_all(engine)
    try:
        service = AcademicSyncService(create_session_factory(engine))
        service.sync(normalize_collection(source.collect("initial")))
        service.sync(normalize_collection(source.collect("initial")))
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).count() == 2
            assert session.query(AssignmentORM).count() == 1
            assert session.query(AnnouncementORM).count() == 1
            assert session.query(RemoteFileORM).count() == 1
    finally:
        engine.dispose()
