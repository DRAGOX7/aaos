from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from pytest import MonkeyPatch
from sqlalchemy import Engine

from aaos.academic.models import CanonicalSnapshot
from aaos.academic.normalization import normalize_collection
from aaos.academic.source import CompleteSourceSnapshot, RawAssignment, RawCourse
from aaos.academic.sync import (
    AcademicSyncService,
    SynchronizationConflict,
    SynchronizationValidationError,
)
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
    assignments: tuple[RawAssignment, ...] = ()
    if assignment_id is not None:
        assignments = (
            RawAssignment(
                source_id=assignment_id,
                course_source_id=course_id,
                title=assignment_title,
            ),
        )
    return normalize_collection(
        CompleteSourceSnapshot(
            source_system=namespace,
            courses=(RawCourse(source_id=course_id, title=course_title),),
            assignments=assignments,
        )
    )


def empty_snapshot(namespace: str = "mock:primary") -> CanonicalSnapshot:
    return normalize_collection(CompleteSourceSnapshot(source_system=namespace))


def two_course_snapshot(*, child_course_id: str) -> CanonicalSnapshot:
    return normalize_collection(
        CompleteSourceSnapshot(
            source_system="mock:primary",
            courses=(
                RawCourse(source_id="course-a", title="A"),
                RawCourse(source_id="course-b", title="B"),
            ),
            assignments=(
                RawAssignment(
                    source_id="assignment-1",
                    course_source_id=child_course_id,
                    title="Task",
                ),
            ),
        )
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


def test_first_discovery_persists_course_metadata(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        source = normalize_collection(
            CompleteSourceSnapshot(
                source_system="mock:primary",
                courses=(
                    RawCourse(
                        source_id="c1",
                        title="Math",
                        code="MATH",
                        description="Intro",
                    ),
                ),
            )
        )
        service.sync(source)
        with create_session_factory(engine)() as session:
            row = session.query(CourseORM).one()
            assert row.code == "MATH"
            assert row.description == "Intro"
    finally:
        engine.dispose()


def test_forged_canonical_snapshot_is_rejected_before_deactivation(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot())
        forged = CanonicalSnapshot(source_system="mock:primary")
        try:
            service.sync(forged)
        except SynchronizationValidationError:
            pass
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).one().is_active is True
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


def test_disappearance_and_reactivation_are_namespace_scoped(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot("mock:primary"))
        service.sync(empty_snapshot("mock:primary"))
        service.sync(snapshot("mock:other"))
        with create_session_factory(engine)() as session:
            assert (
                session.query(CourseORM).filter_by(source_system="mock:primary").one().is_active
                is False
            )
            assert (
                session.query(CourseORM).filter_by(source_system="mock:other").one().is_active
                is True
            )
        service.sync(snapshot("mock:primary"))
        with create_session_factory(engine)() as session:
            assert (
                session.query(CourseORM).filter_by(source_system="mock:primary").one().is_active
                is True
            )
    finally:
        engine.dispose()


def test_reparent_conflict_rolls_back(tmp_path: Path) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(two_course_snapshot(child_course_id="course-a"))
        with create_session_factory(engine)() as session:
            persisted_course = session.query(CourseORM).filter_by(source_id="course-a").one().id
        try:
            service.sync(two_course_snapshot(child_course_id="course-b"))
        except SynchronizationConflict:
            pass
        with create_session_factory(engine)() as session:
            assignment = session.query(AssignmentORM).one()
            assert assignment.course_id == persisted_course
            assert assignment.is_active is True
    finally:
        engine.dispose()


def test_persistence_failure_rolls_back_prior_deactivation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    service, engine = service_for(tmp_path)
    try:
        service.sync(snapshot())

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("injected failure")

        monkeypatch.setattr(AcademicSyncService, "_upsert_assignments", classmethod(fail))
        try:
            service.sync(empty_snapshot())
        except RuntimeError:
            pass
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).one().is_active is True
            assert session.query(AssignmentORM).one().is_active is True
    finally:
        engine.dispose()
