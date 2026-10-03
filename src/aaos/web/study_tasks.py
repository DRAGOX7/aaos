"""Browser view of local study tasks (read-only)."""

from __future__ import annotations

from collections.abc import Iterable

from aaos.study_tasks.contracts import StudyTask, StudyTaskStatus
from aaos.web.contracts import StudyTaskListResponse, StudyTaskView


def study_task_list_response(tasks: Iterable[StudyTask]) -> StudyTaskListResponse:
    """Open tasks first, newest first within each group."""
    ordered = sorted(tasks, key=lambda task: (task.status is not StudyTaskStatus.OPEN, -task.id))
    return StudyTaskListResponse(
        study_tasks=tuple(
            StudyTaskView(
                id=task.id,
                title=task.title,
                status=task.status.value,
                priority=task.priority.value,
                course_id=task.course_id,
                course_title=task.course_title,
                assignment_id=task.assignment_id,
                assignment_title=task.assignment_title,
                due_at=task.due_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            for task in ordered
        )
    )
