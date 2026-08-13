from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
    UTCDateTime,
)
from aaos.persistence.database import create_database_engine, create_session_factory


def test_phase1_metadata_contains_exact_domain_tables() -> None:
    assert set(CourseORM.metadata.tables) == {
        "courses",
        "assignments",
        "announcements",
        "remote_files",
    }


def test_source_identity_is_unique_per_entity_table(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "domain.db")
    CourseORM.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            session.add_all(
                [
                    CourseORM(source_system="mock:primary", source_id="same", title="One"),
                    CourseORM(source_system="mock:primary", source_id="same", title="Duplicate"),
                ]
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_same_source_id_is_allowed_across_tables(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "domain.db")
    CourseORM.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            course = CourseORM(source_system="mock:primary", source_id="same", title="One")
            session.add(course)
            session.flush()
            session.add(
                AssignmentORM(
                    source_system="mock:primary",
                    source_id="same",
                    course_id=course.id,
                    title="Assignment",
                )
            )
            session.commit()
    finally:
        engine.dispose()


def test_child_foreign_key_is_enforced(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "domain.db")
    CourseORM.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            session.add(
                AssignmentORM(
                    source_system="mock:primary",
                    source_id="a1",
                    course_id=999,
                    title="Orphan",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_active_defaults_and_indexes_are_present(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "domain.db")
    CourseORM.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            course = CourseORM(source_system="mock:primary", source_id="c1", title="One")
            session.add(course)
            session.commit()
            assert course.is_active is True
        table_indexes = inspect(engine).get_indexes("assignments")
        names = {index["name"] for index in table_indexes}
        assert "ix_assignments_course_active" in names
    finally:
        engine.dispose()


def test_utc_datetime_rejects_naive_and_normalizes_aware() -> None:
    utc_type = UTCDateTime()
    with pytest.raises(ValueError, match="timezone"):
        utc_type.process_bind_param(datetime(2026, 8, 13, 12), None)

    value = datetime(2026, 8, 13, 15, tzinfo=timezone(timedelta(hours=3)))
    stored = utc_type.process_bind_param(value, None)
    assert stored == "2026-08-13T12:00:00Z"
    restored = utc_type.process_result_value(stored, None)
    assert restored == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def test_utc_datetime_accepts_none() -> None:
    utc_type = UTCDateTime()
    assert utc_type.process_bind_param(None, None) is None
    assert utc_type.process_result_value(None, None) is None


def test_all_domain_models_share_metadata() -> None:
    assert AssignmentORM.metadata is CourseORM.metadata
    assert AnnouncementORM.metadata is CourseORM.metadata
    assert RemoteFileORM.metadata is CourseORM.metadata
