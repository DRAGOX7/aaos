from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from sqlalchemy import Engine

from aaos.academic.models import (
    CanonicalAssignment,
    CanonicalCourse,
    CanonicalSnapshot,
)
from aaos.academic.sync import AcademicSyncService
from aaos.persistence.academic_models import AssignmentORM, CourseORM
from aaos.persistence.database import create_database_engine, create_session_factory

SessionService: TypeAlias = tuple[AcademicSyncService, Engine]


def service_for(tmp_path: Path) -> SessionService:
    engine = create_database_engine(tmp_path / "sync.db")
    CourseORM.metadata.create_all(engine)
    factory = create_session_factory(engine)
    return AcademicSyncService(factory), engine


def snapshot(
    namespace: str = "mock:primary",
    *,
    course_id: str = "c1",
    course_title: str = "Math",
    assignment_id: str | None = "a1",
    assignment_title: str = "Task",
) -> CanonicalSnapshot:
    assignments: tuple[CanonicalAssignment, ...] = ()
    if assignment_id is not None:
        assignments = (
            CanonicalAssignment(
                source_system=namespace,
                source_id=assignment_id,
                course_source_id=course_id,
                title=assignment_title,
            ),
        )
    return CanonicalSnapshot(
        source_system=namespace,
        courses=(
            CanonicalCourse(source_system=namespace, source_id=course_id, title=course_title),
        ),
        assignments=assignments,
    )


def test_initial_and_repeated_sync_are_idempotent(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        first = snapshot()
        service.sync(first)
        service.sync(first)
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).count() == 1
            assert session.query(AssignmentORM).count() == 1
    finally:
        engine.dispose()


def test_changed_mutable_fields_preserve_internal_ids(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot())
        with create_session_factory(engine)() as session:
            course_id = session.query(CourseORM).one().id
            assignment_id = session.query(AssignmentORM).one().id
        service.sync(snapshot(course_title="Updated", assignment_title="Updated task"))
        with create_session_factory(engine)() as session:
            course = session.query(CourseORM).one()
            assignment = session.query(AssignmentORM).one()
            assert course.id == course_id
            assert assignment.id == assignment_id
            assert course.title == "Updated"
            assert assignment.title == "Updated task"
    finally:
        engine.dispose()


def test_new_record_is_inserted(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot(assignment_id=None))
        service.sync(snapshot(assignment_id="a2"))
        with create_session_factory(engine)() as session:
            assert session.query(AssignmentORM).count() == 1
            assert session.query(AssignmentORM).one().source_id == "a2"
    finally:
        engine.dispose()


def test_independent_namespaces_with_colliding_ids_do_not_collide(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot("mock:one"))
        service.sync(snapshot("moodle:just"))
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).count() == 2
            assert session.query(AssignmentORM).count() == 2
    finally:
        engine.dispose()
