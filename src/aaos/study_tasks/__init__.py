"""Trusted local study-task domain."""

from typing import TYPE_CHECKING, Any

from .contracts import (
    StudyTask,
    StudyTaskIdempotencyConflict,
    StudyTaskNotFoundError,
    StudyTaskPriority,
    StudyTaskStatus,
    StudyTaskValidationError,
)

if TYPE_CHECKING:
    from .repository import StudyTaskRepository
    from .service import StudyTaskService

__all__ = [
    "StudyTask",
    "StudyTaskIdempotencyConflict",
    "StudyTaskNotFoundError",
    "StudyTaskPriority",
    "StudyTaskRepository",
    "StudyTaskService",
    "StudyTaskStatus",
    "StudyTaskValidationError",
]


def __getattr__(name: str) -> Any:
    """Load database-backed exports only when requested, keeping imports inert."""
    if name == "StudyTaskRepository":
        from .repository import StudyTaskRepository

        return StudyTaskRepository
    if name == "StudyTaskService":
        from .service import StudyTaskService

        return StudyTaskService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
