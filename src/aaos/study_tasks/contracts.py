"""Closed contracts for local student-owned study tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class StudyTaskStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"


class StudyTaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class StudyTaskErrorCode(StrEnum):
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    COURSE_NOT_FOUND = "COURSE_NOT_FOUND"
    ASSIGNMENT_NOT_FOUND = "ASSIGNMENT_NOT_FOUND"
    INVALID_MUTATION = "INVALID_MUTATION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STALE_CHANGE = "STALE_CHANGE"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"


class StudyTaskError(RuntimeError):
    def __init__(self, code: StudyTaskErrorCode) -> None:
        if not isinstance(code, StudyTaskErrorCode):
            raise TypeError("code must be a StudyTaskErrorCode")
        self.code = code
        super().__init__(code.value)


class StudyTaskNotFoundError(StudyTaskError):
    def __init__(self) -> None:
        super().__init__(StudyTaskErrorCode.TASK_NOT_FOUND)


class StudyTaskReferenceNotFoundError(StudyTaskError):
    def __init__(self, code: StudyTaskErrorCode) -> None:
        if code not in {
            StudyTaskErrorCode.COURSE_NOT_FOUND,
            StudyTaskErrorCode.ASSIGNMENT_NOT_FOUND,
        }:
            raise ValueError("code must identify a missing canonical academic reference")
        super().__init__(code)


class StudyTaskValidationError(StudyTaskError):
    def __init__(self) -> None:
        super().__init__(StudyTaskErrorCode.INVALID_MUTATION)


class StudyTaskIdempotencyConflict(StudyTaskError):
    def __init__(self) -> None:
        super().__init__(StudyTaskErrorCode.IDEMPOTENCY_CONFLICT)


class StudyTaskStaleChangeError(StudyTaskError):
    def __init__(self) -> None:
        super().__init__(StudyTaskErrorCode.STALE_CHANGE)


class StudyTaskPersistenceError(StudyTaskError):
    def __init__(self) -> None:
        super().__init__(StudyTaskErrorCode.PERSISTENCE_FAILURE)


def _positive_id(value: int | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class StudyTask:
    """Sanitized domain projection; no ORM or persistence state escapes."""

    id: int
    title: str
    status: StudyTaskStatus
    priority: StudyTaskPriority
    course_id: int | None
    course_title: str | None
    assignment_id: int | None
    assignment_title: str | None
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _positive_id(self.id, "id")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 500:
            raise ValueError("title must be bounded non-blank text")
        if not isinstance(self.status, StudyTaskStatus):
            raise TypeError("status must be a StudyTaskStatus")
        if not isinstance(self.priority, StudyTaskPriority):
            raise TypeError("priority must be a StudyTaskPriority")
        _positive_id(self.course_id, "course_id")
        _positive_id(self.assignment_id, "assignment_id")
        for name, value in (
            ("course_title", self.course_title),
            ("assignment_title", self.assignment_title),
        ):
            if value is not None and (not isinstance(value, str) or len(value) > 500):
                raise ValueError(f"{name} must be bounded text or None")
        if self.due_at is not None:
            object.__setattr__(self, "due_at", _aware_utc(self.due_at, "due_at"))
        created_at = _aware_utc(self.created_at, "created_at")
        updated_at = _aware_utc(self.updated_at, "updated_at")
        if updated_at < created_at:
            raise ValueError("updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)


__all__ = [
    "StudyTask",
    "StudyTaskError",
    "StudyTaskErrorCode",
    "StudyTaskIdempotencyConflict",
    "StudyTaskNotFoundError",
    "StudyTaskPersistenceError",
    "StudyTaskPriority",
    "StudyTaskStaleChangeError",
    "StudyTaskReferenceNotFoundError",
    "StudyTaskStatus",
    "StudyTaskValidationError",
]
