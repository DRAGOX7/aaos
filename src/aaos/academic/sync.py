"""Atomic namespace-scoped synchronization into the academic schema."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.academic.models import _NORMALIZED_SNAPSHOT_TOKEN, CanonicalSnapshot
from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)

# Courses first: items archive only together with their course.
_ARCHIVABLE_MODELS: tuple[type, ...] = (CourseORM, AssignmentORM, AnnouncementORM, RemoteFileORM)


class SynchronizationConflict(ValueError):
    """Raised when an existing child resolves to a different parent."""


class SynchronizationPersistenceError(RuntimeError):
    """Raised for a persistence failure without exposing native details."""


class SynchronizationValidationError(ValueError):
    """Raised when a caller bypasses source collection normalization."""


class AcademicSyncService:
    """Apply one validated complete snapshot in one database transaction."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def sync(self, snapshot: CanonicalSnapshot) -> None:
        if snapshot._validation_token is not _NORMALIZED_SNAPSHOT_TOKEN:
            raise SynchronizationValidationError(
                "snapshot was not produced by complete source normalization"
            )
        session = self._session_factory()
        try:
            with session.begin():
                self.sync_in_session(session, snapshot)
        except SynchronizationConflict:
            session.rollback()
            raise
        except SQLAlchemyError:
            session.rollback()
            raise SynchronizationPersistenceError(
                "academic synchronization persistence failed"
            ) from None
        finally:
            session.close()

    def sync_in_session(self, session: Session, snapshot: CanonicalSnapshot) -> None:
        """Apply a normalized snapshot inside a caller-owned transaction."""
        if not isinstance(session, Session):
            raise TypeError("session must be a SQLAlchemy Session")
        if snapshot._validation_token is not _NORMALIZED_SNAPSHOT_TOKEN:
            raise SynchronizationValidationError(
                "snapshot was not produced by complete source normalization"
            )
        previously_active = self._active_ids(session, snapshot.source_system)
        self._deactivate_namespace(session, snapshot.source_system)
        courses = self._upsert_courses(session, snapshot)
        self._upsert_assignments(session, snapshot, courses)
        self._upsert_announcements(session, snapshot, courses)
        self._upsert_remote_files(session, snapshot, courses)
        self._apply_archive_state(session, snapshot.source_system, previously_active)

    @staticmethod
    def _active_ids(session: Session, source_system: str) -> dict[type, frozenset[int]]:
        return {
            model: frozenset(
                session.scalars(
                    select(model.id).where(
                        model.source_system == source_system, model.is_active.is_(True)
                    )
                )
            )
            for model in _ARCHIVABLE_MODELS
        }

    def _apply_archive_state(
        self,
        session: Session,
        source_system: str,
        previously_active: dict[type, frozenset[int]],
    ) -> None:
        """Archive courses this complete sync stopped listing, with their items.

        A course that was current and is no longer listed becomes past, and so
        does every item that disappeared together with it. An item that
        disappears while its course stays current is removed, not archived.
        Anything listed again is current. Earlier archive times are kept.
        """
        now = self._clock()
        archived_now: set[int] = set()
        active_courses: set[int] = set()
        for course in session.scalars(
            select(CourseORM).where(CourseORM.source_system == source_system)
        ):
            if course.is_active:
                course.archived_at = None
                active_courses.add(course.id)
            elif course.id in previously_active[CourseORM]:
                course.archived_at = now
                archived_now.add(course.id)
        for model in _ARCHIVABLE_MODELS[1:]:
            for item in session.scalars(select(model).where(model.source_system == source_system)):
                if item.is_active or item.course_id in active_courses:
                    item.archived_at = None
                elif item.id in previously_active[model] and item.course_id in archived_now:
                    item.archived_at = now

    @staticmethod
    def _deactivate_namespace(session: Session, source_system: str) -> None:
        for model in (CourseORM, AssignmentORM, AnnouncementORM, RemoteFileORM):
            session.execute(
                update(model).where(model.source_system == source_system).values(is_active=False)
            )

    @staticmethod
    def _upsert_courses(session: Session, snapshot: CanonicalSnapshot) -> dict[str, int]:
        result: dict[str, int] = {}
        for record in snapshot.courses:
            row = (
                session.query(CourseORM)
                .filter_by(source_system=snapshot.source_system, source_id=record.source_id)
                .one_or_none()
            )
            if row is None:
                row = CourseORM(
                    source_system=snapshot.source_system,
                    source_id=record.source_id,
                    title=record.title,
                    code=record.code,
                    description=record.description,
                    source_updated_at=record.source_updated_at,
                )
                session.add(row)
                session.flush()
            else:
                row.title = record.title
                row.code = record.code
                row.description = record.description
                row.source_updated_at = record.source_updated_at
                row.is_active = True
            result[record.source_id] = row.id
        return result

    @staticmethod
    def _parent_id(
        row: AssignmentORM | AnnouncementORM | RemoteFileORM,
        expected_course_id: int,
    ) -> None:
        if row.course_id != expected_course_id:
            raise SynchronizationConflict("child parent identity cannot change")

    @classmethod
    def _upsert_assignments(
        cls,
        session: Session,
        snapshot: CanonicalSnapshot,
        courses: dict[str, int],
    ) -> None:
        for record in snapshot.assignments:
            course_id = courses[record.course_source_id]
            row = (
                session.query(AssignmentORM)
                .filter_by(source_system=snapshot.source_system, source_id=record.source_id)
                .one_or_none()
            )
            if row is None:
                row = AssignmentORM(
                    source_system=snapshot.source_system,
                    source_id=record.source_id,
                    course_id=course_id,
                )
                session.add(row)
            else:
                cls._parent_id(row, course_id)
            row.title = record.title
            row.description = record.description
            row.due_at = record.due_at
            row.source_updated_at = record.source_updated_at
            row.is_active = True

    @classmethod
    def _upsert_announcements(
        cls,
        session: Session,
        snapshot: CanonicalSnapshot,
        courses: dict[str, int],
    ) -> None:
        for record in snapshot.announcements:
            course_id = courses[record.course_source_id]
            row = (
                session.query(AnnouncementORM)
                .filter_by(source_system=snapshot.source_system, source_id=record.source_id)
                .one_or_none()
            )
            if row is None:
                row = AnnouncementORM(
                    source_system=snapshot.source_system,
                    source_id=record.source_id,
                    course_id=course_id,
                )
                session.add(row)
            else:
                cls._parent_id(row, course_id)
            row.title = record.title
            row.body = record.body
            row.published_at = record.published_at
            row.source_updated_at = record.source_updated_at
            row.is_active = True

    @classmethod
    def _upsert_remote_files(
        cls,
        session: Session,
        snapshot: CanonicalSnapshot,
        courses: dict[str, int],
    ) -> None:
        for record in snapshot.remote_files:
            course_id = courses[record.course_source_id]
            row = (
                session.query(RemoteFileORM)
                .filter_by(source_system=snapshot.source_system, source_id=record.source_id)
                .one_or_none()
            )
            if row is None:
                row = RemoteFileORM(
                    source_system=snapshot.source_system,
                    source_id=record.source_id,
                    course_id=course_id,
                )
                session.add(row)
            else:
                cls._parent_id(row, course_id)
            row.title = record.title
            row.remote_url = record.remote_url
            row.source_updated_at = record.source_updated_at
            row.is_active = True
