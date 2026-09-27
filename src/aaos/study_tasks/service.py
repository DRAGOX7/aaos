"""Validation, canonicalization, and idempotency for local StudyTasks."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from .contracts import (
    StudyTask,
    StudyTaskNotFoundError,
    StudyTaskPersistenceError,
    StudyTaskPriority,
    StudyTaskStatus,
    StudyTaskValidationError,
)
from .repository import StudyTaskRepository

MAX_STUDY_TASKS = 50
MAX_STUDY_TASK_TITLE_CHARACTERS = 500


def _fingerprint(operation: str, values: dict[str, object]) -> str:
    payload = json.dumps(
        {"operation": operation, **values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StudyTaskService:
    """Own bounded StudyTask rules above the transactional repository."""

    def __init__(
        self,
        repository: StudyTaskRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(repository, StudyTaskRepository):
            raise TypeError("repository must be a StudyTaskRepository")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._repository = repository
        self._clock = clock

    def create(
        self,
        *,
        request_id: UUID,
        title: str | None = None,
        course_id: int | None = None,
        assignment_id: int | None = None,
        priority: StudyTaskPriority = StudyTaskPriority.NORMAL,
    ) -> StudyTask:
        self._validate_request_id(request_id)
        normalized_title = self._title(title, required=assignment_id is None)
        self._validate_link_id(course_id)
        self._validate_link_id(assignment_id)
        if course_id is not None and assignment_id is not None:
            raise StudyTaskValidationError()
        self._validate_priority(priority)
        now = self._now()
        fingerprint = _fingerprint(
            "CREATE",
            {
                "assignment_id": assignment_id,
                "course_id": course_id,
                "priority": priority.value,
                "title": normalized_title,
            },
        )
        try:
            return self._repository.create_idempotent(
                request_id=request_id,
                fingerprint=fingerprint,
                title=normalized_title,
                status=StudyTaskStatus.OPEN,
                priority=priority,
                course_id=course_id,
                assignment_id=assignment_id,
                now=now,
            )
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None

    def get(self, task_id: int) -> StudyTask:
        self._validate_task_id(task_id)
        try:
            task = self._repository.get(task_id)
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None
        if task is None:
            raise StudyTaskNotFoundError()
        return task

    def list(
        self,
        *,
        status: StudyTaskStatus | None = None,
        course_id: int | None = None,
        limit: int = MAX_STUDY_TASKS,
    ) -> tuple[StudyTask, ...]:
        if status is not None and not isinstance(status, StudyTaskStatus):
            raise StudyTaskValidationError()
        self._validate_link_id(course_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_STUDY_TASKS
        ):
            raise StudyTaskValidationError()
        try:
            return self._repository.list(status=status, course_id=course_id, limit=limit)
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None

    def update(
        self,
        task_id: int,
        *,
        request_id: UUID,
        title: str | None = None,
        priority: StudyTaskPriority | None = None,
    ) -> StudyTask:
        self._validate_request_id(request_id)
        self._validate_task_id(task_id)
        normalized_title = self._title(title, required=False)
        self._validate_priority(priority, optional=True)
        if normalized_title is None and priority is None:
            raise StudyTaskValidationError()
        fingerprint = _fingerprint(
            "UPDATE",
            {
                "priority": priority.value if priority is not None else None,
                "task_id": task_id,
                "title": normalized_title,
            },
        )
        try:
            return self._repository.update_idempotent(
                task_id,
                request_id=request_id,
                fingerprint=fingerprint,
                title=normalized_title,
                priority=priority,
                now=self._now(),
            )
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None

    def apply_moodle_deadline_change(
        self,
        task_id: int,
        *,
        request_id: UUID,
        assignment_id: int,
        expected_due_at: datetime,
        source_change_id: str,
        source_from_snapshot_id: int,
        source_to_snapshot_id: int,
    ) -> StudyTask:
        """Acknowledge a current assignment deadline change on one linked task."""

        self._validate_request_id(request_id)
        self._validate_task_id(task_id)
        self._validate_link_id(assignment_id)
        if (
            not isinstance(expected_due_at, datetime)
            or expected_due_at.tzinfo is None
            or expected_due_at.utcoffset() is None
            or not isinstance(source_change_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", source_change_id)
        ):
            raise StudyTaskValidationError()
        self._validate_task_id(source_from_snapshot_id)
        self._validate_task_id(source_to_snapshot_id)
        if source_from_snapshot_id >= source_to_snapshot_id:
            raise StudyTaskValidationError()

        normalized_due_at = expected_due_at.astimezone(timezone.utc)
        now = self._now()
        fingerprint = _fingerprint(
            "APPLY_CHANGE",
            {
                "assignment_id": assignment_id,
                "expected_due_at": normalized_due_at.isoformat(),
                "source_change_id": source_change_id,
                "source_changed_field": "due_at",
                "source_from_snapshot_id": source_from_snapshot_id,
                "source_to_snapshot_id": source_to_snapshot_id,
                "task_id": task_id,
            },
        )
        try:
            return self._repository.apply_moodle_deadline_change_idempotent(
                task_id,
                request_id=request_id,
                fingerprint=fingerprint,
                assignment_id=assignment_id,
                expected_due_at=normalized_due_at,
                source_change_id=source_change_id,
                source_from_snapshot_id=source_from_snapshot_id,
                source_to_snapshot_id=source_to_snapshot_id,
                now=now,
            )
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None

    def complete(self, task_id: int, *, request_id: UUID) -> StudyTask:
        self._validate_request_id(request_id)
        self._validate_task_id(task_id)
        fingerprint = _fingerprint("COMPLETE", {"task_id": task_id})
        try:
            return self._repository.complete_idempotent(
                task_id,
                request_id=request_id,
                fingerprint=fingerprint,
                now=self._now(),
            )
        except SQLAlchemyError:
            raise StudyTaskPersistenceError() from None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise StudyTaskValidationError()
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_request_id(value: UUID) -> None:
        if not isinstance(value, UUID):
            raise StudyTaskValidationError()

    @staticmethod
    def _validate_task_id(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise StudyTaskValidationError()

    @staticmethod
    def _validate_link_id(value: int | None) -> None:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise StudyTaskValidationError()

    @staticmethod
    def _validate_priority(
        value: StudyTaskPriority | None,
        *,
        optional: bool = False,
    ) -> None:
        if value is None and optional:
            return
        if not isinstance(value, StudyTaskPriority):
            raise StudyTaskValidationError()

    @staticmethod
    def _title(value: str | None, *, required: bool) -> str | None:
        if value is None:
            if required:
                raise StudyTaskValidationError()
            return None
        if not isinstance(value, str):
            raise StudyTaskValidationError()
        normalized = value.strip()
        if not normalized or len(normalized) > MAX_STUDY_TASK_TITLE_CHARACTERS:
            raise StudyTaskValidationError()
        return normalized


__all__ = ["MAX_STUDY_TASKS", "StudyTaskService"]
