from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.study_tasks.contracts import (
    StudyTaskErrorCode,
    StudyTaskIdempotencyConflict,
    StudyTaskNotFoundError,
    StudyTaskPriority,
    StudyTaskReferenceNotFoundError,
    StudyTaskStatus,
    StudyTaskValidationError,
)
from aaos.study_tasks.repository import StudyTaskRepository
from aaos.study_tasks.service import StudyTaskService

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def service(tmp_path: Path) -> Iterator[StudyTaskService]:
    database_path = tmp_path / "study-tasks.db"
    engine = create_database_engine(database_path)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO courses (id, source_system, source_id, title, is_active) "
            "VALUES (41, 'test', 'course-41', 'Algorithms', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO assignments "
            "(id, course_id, source_system, source_id, title, due_at, is_active) "
            "VALUES (73, 41, 'test', 'assignment-73', 'Proof set', "
            "'2026-10-01T12:00:00Z', 1)"
        )
    repository = StudyTaskRepository(create_session_factory(engine))
    try:
        yield StudyTaskService(repository, clock=lambda: NOW)
    finally:
        engine.dispose()


def test_create_read_and_list_study_tasks(service: StudyTaskService) -> None:
    created = service.create(
        title="Review lecture notes",
        request_id=UUID("00000000-0000-4000-8000-000000000001"),
    )

    assert created.id > 0
    assert created.title == "Review lecture notes"
    assert created.status is StudyTaskStatus.OPEN
    assert created.priority is StudyTaskPriority.NORMAL
    assert service.get(created.id) == created
    assert service.list(status=StudyTaskStatus.OPEN) == (created,)
    assert service.list(status=StudyTaskStatus.COMPLETED) == ()


def test_complete_and_update_only_allowed_fields(service: StudyTaskService) -> None:
    task = service.create(
        title="Read chapter",
        request_id=UUID("00000000-0000-4000-8000-000000000002"),
    )

    renamed = service.update(
        task.id,
        request_id=UUID("00000000-0000-4000-8000-000000000003"),
        title="Read chapter 4",
        priority=StudyTaskPriority.HIGH,
    )
    completed = service.complete(
        renamed.id,
        request_id=UUID("00000000-0000-4000-8000-000000000004"),
    )

    assert renamed.title == "Read chapter 4"
    assert renamed.priority is StudyTaskPriority.HIGH
    assert completed.status is StudyTaskStatus.COMPLETED
    assert service.list(status=StudyTaskStatus.OPEN) == ()
    assert service.list(status=StudyTaskStatus.COMPLETED) == (completed,)


def test_assignment_link_uses_canonical_course_and_due_date(service: StudyTaskService) -> None:
    task = service.create(
        assignment_id=73,
        request_id=UUID("00000000-0000-4000-8000-000000000005"),
    )

    assert task.title == "Proof set"
    assert task.assignment_id == 73
    assert task.assignment_title == "Proof set"
    assert task.course_id == 41
    assert task.course_title == "Algorithms"
    assert task.due_at == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert service.list(course_id=41) == (task,)


def test_course_link_is_canonical_and_assignment_link_is_not_duplicated(
    service: StudyTaskService,
) -> None:
    course_task = service.create(
        title="Review algorithms",
        course_id=41,
        request_id=UUID("00000000-0000-4000-8000-000000000006"),
    )
    with pytest.raises(StudyTaskValidationError):
        service.create(
            title="Conflicting links",
            course_id=41,
            assignment_id=73,
            request_id=UUID("00000000-0000-4000-8000-000000000007"),
        )

    assert course_task.course_id == 41
    assert course_task.course_title == "Algorithms"
    assert course_task.assignment_id is None


def test_invalid_update_and_noncanonical_priority_are_rejected(
    service: StudyTaskService,
) -> None:
    task = service.create(
        title="Review",
        request_id=UUID("00000000-0000-4000-8000-000000000008"),
    )

    with pytest.raises(StudyTaskValidationError):
        service.update(task.id, request_id=UUID("00000000-0000-4000-8000-000000000009"))
    with pytest.raises(StudyTaskValidationError):
        service.update(
            task.id,
            request_id=UUID("00000000-0000-4000-8000-000000000010"),
            priority="URGENT",  # type: ignore[arg-type]
        )


def test_missing_entities_have_typed_not_found_results(service: StudyTaskService) -> None:
    with pytest.raises(StudyTaskNotFoundError):
        service.get(999)
    with pytest.raises(StudyTaskReferenceNotFoundError) as error:
        service.create(
            assignment_id=999,
            request_id=UUID("00000000-0000-4000-8000-000000000011"),
        )
    assert error.value.code is StudyTaskErrorCode.ASSIGNMENT_NOT_FOUND


def test_idempotency_replays_mutations_and_allows_distinct_similar_tasks(
    service: StudyTaskService,
) -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000012")
    first = service.create(title="Read chapter", request_id=request_id)
    replay = service.create(title="Read chapter", request_id=request_id)
    second = service.create(
        title="Read chapter",
        request_id=UUID("00000000-0000-4000-8000-000000000013"),
    )

    assert replay.id == first.id
    assert second.id != first.id
    assert service.list() == (first, second)


def test_idempotency_key_cannot_be_reused_for_different_intent(
    service: StudyTaskService,
) -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000014")
    service.create(title="Read chapter", request_id=request_id)

    with pytest.raises(StudyTaskIdempotencyConflict):
        service.create(title="Read another chapter", request_id=request_id)


def test_replayed_update_and_completion_do_not_apply_a_second_mutation(
    service: StudyTaskService,
) -> None:
    task = service.create(
        title="Read chapter",
        request_id=UUID("00000000-0000-4000-8000-000000000015"),
    )
    update_id = UUID("00000000-0000-4000-8000-000000000016")
    complete_id = UUID("00000000-0000-4000-8000-000000000017")

    updated = service.update(task.id, request_id=update_id, title="Read chapter 5")
    assert service.update(task.id, request_id=update_id, title="Read chapter 5") == updated
    completed = service.complete(updated.id, request_id=complete_id)
    assert service.complete(updated.id, request_id=complete_id) == completed
    assert completed.status is StudyTaskStatus.COMPLETED
