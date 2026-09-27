"""Typed, transactional persistence boundary for local StudyTask records."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from aaos.persistence.academic_models import AssignmentORM, CourseORM
from aaos.persistence.moodle_sync_models import MoodleSyncSnapshotORM
from aaos.persistence.study_task_models import StudyTaskMutationORM, StudyTaskORM

from .contracts import (
    StudyTask,
    StudyTaskAmbiguousAssignmentError,
    StudyTaskErrorCode,
    StudyTaskIdempotencyConflict,
    StudyTaskNotFoundError,
    StudyTaskPriority,
    StudyTaskReferenceNotFoundError,
    StudyTaskStaleChangeError,
    StudyTaskStatus,
)

_Mutation = Callable[[Session], int]


@contextmanager
def _transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _task_select() -> Any:
    direct_course = aliased(CourseORM)
    assignment = aliased(AssignmentORM)
    assignment_course = aliased(CourseORM)
    return (
        select(StudyTaskORM, direct_course, assignment, assignment_course)
        .outerjoin(direct_course, direct_course.id == StudyTaskORM.course_id)
        .outerjoin(assignment, assignment.id == StudyTaskORM.assignment_id)
        .outerjoin(assignment_course, assignment_course.id == assignment.course_id)
    )


def _project(row: Any) -> StudyTask:
    task_row, direct_course, assignment, assignment_course = row
    course = direct_course or assignment_course
    return StudyTask(
        id=task_row.id,
        title=task_row.title,
        status=StudyTaskStatus(task_row.status),
        priority=StudyTaskPriority(task_row.priority),
        course_id=course.id if course is not None else None,
        course_title=course.title if course is not None else None,
        assignment_id=assignment.id if assignment is not None else None,
        assignment_title=assignment.title if assignment is not None else None,
        due_at=assignment.due_at if assignment is not None else None,
        created_at=task_row.created_at,
        updated_at=task_row.updated_at,
    )


def _task_result_snapshot(task: StudyTask) -> str:
    return json.dumps(
        {
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "priority": task.priority.value,
            "course_id": task.course_id,
            "course_title": task.course_title,
            "assignment_id": task.assignment_id,
            "assignment_title": task.assignment_title,
            "due_at": task.due_at.isoformat() if task.due_at is not None else None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _restore_task_result_snapshot(snapshot: str) -> StudyTask:
    expected = {
        "id",
        "title",
        "status",
        "priority",
        "course_id",
        "course_title",
        "assignment_id",
        "assignment_title",
        "due_at",
        "created_at",
        "updated_at",
    }
    try:
        payload = json.loads(snapshot)
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("invalid StudyTask result snapshot fields")
        due_at = payload["due_at"]
        return StudyTask(
            id=payload["id"],
            title=payload["title"],
            status=StudyTaskStatus(payload["status"]),
            priority=StudyTaskPriority(payload["priority"]),
            course_id=payload["course_id"],
            course_title=payload["course_title"],
            assignment_id=payload["assignment_id"],
            assignment_title=payload["assignment_title"],
            due_at=datetime.fromisoformat(due_at) if due_at is not None else None,
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("StudyTask mutation contains an invalid saved result") from error


class StudyTaskRepository:
    """Own task sessions and expose only bounded domain persistence methods."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        if not isinstance(session_factory, sessionmaker):
            raise TypeError("session_factory must be a SQLAlchemy sessionmaker")
        self._session_factory = session_factory

    def create_idempotent(
        self,
        *,
        request_id: UUID,
        fingerprint: str,
        title: str | None,
        status: StudyTaskStatus,
        priority: StudyTaskPriority,
        course_id: int | None,
        assignment_id: int | None,
        now: datetime,
    ) -> StudyTask:
        def create(session: Session) -> int:
            actual_title = title
            if assignment_id is not None:
                linked = session.execute(
                    select(AssignmentORM, CourseORM)
                    .join(CourseORM, CourseORM.id == AssignmentORM.course_id)
                    .where(
                        AssignmentORM.id == assignment_id,
                        AssignmentORM.is_active.is_(True),
                        CourseORM.is_active.is_(True),
                    )
                ).first()
                if linked is None:
                    raise StudyTaskReferenceNotFoundError(StudyTaskErrorCode.ASSIGNMENT_NOT_FOUND)
                if actual_title is None:
                    actual_title = linked[0].title
            elif course_id is not None:
                course = session.scalar(
                    select(CourseORM).where(
                        CourseORM.id == course_id,
                        CourseORM.is_active.is_(True),
                    )
                )
                if course is None:
                    raise StudyTaskReferenceNotFoundError(StudyTaskErrorCode.COURSE_NOT_FOUND)

            task_row = StudyTaskORM(
                title=actual_title,
                status=status.value,
                priority=priority.value,
                course_id=course_id,
                assignment_id=assignment_id,
                created_at=now,
                updated_at=now,
            )
            session.add(task_row)
            session.flush()
            return task_row.id

        return self._idempotent_mutation(
            request_id=request_id,
            operation="CREATE",
            fingerprint=fingerprint,
            now=now,
            mutation=create,
        )

    def update_idempotent(
        self,
        task_id: int,
        *,
        request_id: UUID,
        fingerprint: str,
        title: str | None,
        priority: StudyTaskPriority | None,
        now: datetime,
    ) -> StudyTask:
        def update_task(session: Session) -> int:
            row = session.get(StudyTaskORM, task_id)
            if row is None:
                raise StudyTaskNotFoundError()
            if title is not None:
                row.title = title
            if priority is not None:
                row.priority = priority.value
            row.updated_at = max(now, row.updated_at)
            session.flush()
            return row.id

        return self._idempotent_mutation(
            request_id=request_id,
            operation="UPDATE",
            fingerprint=fingerprint,
            now=now,
            mutation=update_task,
        )

    def apply_moodle_deadline_change_idempotent(
        self,
        *,
        request_id: UUID,
        fingerprint: str,
        assignment_id: int,
        expected_due_at: datetime,
        source_change_id: str,
        source_from_snapshot_id: int,
        source_to_snapshot_id: int,
        now: datetime,
    ) -> StudyTask:
        def apply_change(session: Session) -> int:
            linked_tasks = tuple(
                session.scalars(
                    select(StudyTaskORM)
                    .where(
                        StudyTaskORM.assignment_id == assignment_id,
                        StudyTaskORM.status == StudyTaskStatus.OPEN.value,
                    )
                    .order_by(StudyTaskORM.id.asc())
                    .limit(2)
                ).all()
            )
            if not linked_tasks:
                raise StudyTaskNotFoundError()
            if len(linked_tasks) > 1:
                raise StudyTaskAmbiguousAssignmentError()
            task = linked_tasks[0]

            assignment = session.get(AssignmentORM, assignment_id)
            if (
                assignment is None
                or not assignment.is_active
                or assignment.due_at != expected_due_at
            ):
                raise StudyTaskStaleChangeError()
            course = session.get(CourseORM, assignment.course_id)
            if course is None or not course.is_active:
                raise StudyTaskStaleChangeError()

            source_snapshots = tuple(
                session.scalars(
                    select(MoodleSyncSnapshotORM)
                    .where(
                        MoodleSyncSnapshotORM.id.in_(
                            (source_from_snapshot_id, source_to_snapshot_id)
                        )
                    )
                    .order_by(MoodleSyncSnapshotORM.id.asc())
                ).all()
            )
            latest_snapshot = session.scalar(
                select(MoodleSyncSnapshotORM)
                .where(MoodleSyncSnapshotORM.source_system == assignment.source_system)
                .order_by(MoodleSyncSnapshotORM.id.desc())
                .limit(1)
            )
            if (
                len(source_snapshots) != 2
                or source_snapshots[0].id != source_from_snapshot_id
                or source_snapshots[1].id != source_to_snapshot_id
                or any(
                    item.source_system != assignment.source_system or item.status != "COMPLETE"
                    for item in source_snapshots
                )
                or latest_snapshot is None
                or latest_snapshot.status != "COMPLETE"
            ):
                raise StudyTaskStaleChangeError()

            task.updated_at = max(now, task.updated_at)
            session.flush()
            return task.id

        return self._idempotent_mutation(
            request_id=request_id,
            operation="APPLY_CHANGE",
            fingerprint=fingerprint,
            now=now,
            mutation=apply_change,
            source_change_id=source_change_id,
            source_from_snapshot_id=source_from_snapshot_id,
            source_to_snapshot_id=source_to_snapshot_id,
            source_changed_field="due_at",
        )

    def complete_idempotent(
        self,
        task_id: int,
        *,
        request_id: UUID,
        fingerprint: str,
        now: datetime,
    ) -> StudyTask:
        def complete_task(session: Session) -> int:
            row = session.get(StudyTaskORM, task_id)
            if row is None:
                raise StudyTaskNotFoundError()
            row.status = StudyTaskStatus.COMPLETED.value
            row.updated_at = max(now, row.updated_at)
            session.flush()
            return row.id

        return self._idempotent_mutation(
            request_id=request_id,
            operation="COMPLETE",
            fingerprint=fingerprint,
            now=now,
            mutation=complete_task,
        )

    def get(self, task_id: int) -> StudyTask | None:
        with self._session_factory() as session:
            row = session.execute(_task_select().where(StudyTaskORM.id == task_id)).first()
            return _project(row) if row is not None else None

    def list(
        self,
        *,
        status: StudyTaskStatus | None,
        course_id: int | None,
        limit: int,
    ) -> tuple[StudyTask, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between one and 50")
        assignment = aliased(AssignmentORM)
        statement = (
            _task_select()
            .outerjoin(assignment, assignment.id == StudyTaskORM.assignment_id)
            .order_by(StudyTaskORM.created_at.asc(), StudyTaskORM.id.asc())
            .limit(limit)
        )
        if status is not None:
            statement = statement.where(StudyTaskORM.status == status.value)
        if course_id is not None:
            statement = statement.where(
                or_(
                    StudyTaskORM.course_id == course_id,
                    and_(
                        StudyTaskORM.assignment_id == assignment.id,
                        assignment.course_id == course_id,
                    ),
                )
            )
        with self._session_factory() as session:
            return tuple(_project(row) for row in session.execute(statement).all())

    def _idempotent_mutation(
        self,
        *,
        request_id: UUID,
        operation: str,
        fingerprint: str,
        now: datetime,
        mutation: _Mutation,
        source_change_id: str | None = None,
        source_from_snapshot_id: int | None = None,
        source_to_snapshot_id: int | None = None,
        source_changed_field: str | None = None,
    ) -> StudyTask:
        try:
            with _transaction(self._session_factory) as session:
                existing = session.get(StudyTaskMutationORM, str(request_id))
                if existing is not None:
                    return self._replay(session, existing, operation, fingerprint)
                task_id = mutation(session)
                result = self._load(session, task_id)
                session.add(
                    StudyTaskMutationORM(
                        request_id=str(request_id),
                        operation=operation,
                        request_fingerprint=fingerprint,
                        study_task_id=task_id,
                        created_at=now,
                        source_change_id=source_change_id,
                        source_from_snapshot_id=source_from_snapshot_id,
                        source_to_snapshot_id=source_to_snapshot_id,
                        source_changed_field=source_changed_field,
                        result_snapshot=(
                            _task_result_snapshot(result) if operation == "APPLY_CHANGE" else None
                        ),
                    )
                )
                session.flush()
                return result
        except IntegrityError:
            with self._session_factory() as session:
                existing = session.get(StudyTaskMutationORM, str(request_id))
                if existing is None:
                    raise
                return self._replay(session, existing, operation, fingerprint)

    def _replay(
        self,
        session: Session,
        mutation: StudyTaskMutationORM,
        operation: str,
        fingerprint: str,
    ) -> StudyTask:
        if mutation.operation != operation or mutation.request_fingerprint != fingerprint:
            raise StudyTaskIdempotencyConflict()
        if operation == "APPLY_CHANGE":
            if mutation.result_snapshot is None:
                raise RuntimeError("StudyTask mutation is missing its saved result")
            return _restore_task_result_snapshot(mutation.result_snapshot)
        return self._load(session, mutation.study_task_id)

    @staticmethod
    def _load(session: Session, task_id: int) -> StudyTask:
        row = session.execute(_task_select().where(StudyTaskORM.id == task_id)).first()
        if row is None:
            raise RuntimeError("StudyTask mutation record references a missing task")
        return _project(row)


__all__ = ["StudyTaskRepository"]
