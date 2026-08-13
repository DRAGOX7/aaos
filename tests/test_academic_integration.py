from __future__ import annotations

import subprocess
import sys
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"


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


def test_integration_lifecycle_and_namespace_isolation(tmp_path: Path) -> None:
    initial = CompleteSourceSnapshot(
        source_system="mock:primary",
        courses=(RawCourse(source_id="c1", title="Math"),),
        assignments=(RawAssignment(source_id="a1", course_source_id="c1", title="First"),),
    )
    changed = CompleteSourceSnapshot(
        source_system="mock:primary",
        courses=(RawCourse(source_id="c1", title="Updated Math"),),
        assignments=(RawAssignment(source_id="a2", course_source_id="c1", title="Second"),),
    )
    other = CompleteSourceSnapshot(
        source_system="mock:other",
        courses=(RawCourse(source_id="c1", title="Other Math"),),
    )
    source = MockAcademicSource(
        {
            "initial": initial,
            "changed": changed,
            "empty": CompleteSourceSnapshot("mock:primary"),
            "other": other,
        }
    )
    engine = create_database_engine(tmp_path / "lifecycle.db")
    CourseORM.metadata.create_all(engine)
    try:
        service = AcademicSyncService(create_session_factory(engine))
        service.sync(normalize_collection(source.collect("initial")))
        service.sync(normalize_collection(source.collect("changed")))
        service.sync(normalize_collection(source.collect("empty")))
        service.sync(normalize_collection(source.collect("other")))
        service.sync(normalize_collection(source.collect("changed")))
        with create_session_factory(engine)() as session:
            primary = session.query(CourseORM).filter_by(source_system="mock:primary").one()
            other_row = session.query(CourseORM).filter_by(source_system="mock:other").one()
            assert primary.title == "Updated Math"
            assert primary.is_active is True
            assert other_row.is_active is True
            assert session.query(AssignmentORM).filter_by(source_id="a1").one().is_active is False
            assert session.query(AssignmentORM).filter_by(source_id="a2").one().is_active is True
    finally:
        engine.dispose()


def test_academic_imports_are_side_effect_free(tmp_path: Path) -> None:
    script = f"import sys; sys.path.insert(0, {str(SOURCE_ROOT)!r}); import aaos.academic.sync"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "data" / "database" / "aaos.db").exists()
