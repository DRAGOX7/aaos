from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from aaos.approval.contracts import ActionId, ActorIdentity
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.tasks.contracts import TaskCreationState, TaskFailureCode, TaskKind, TaskRecord
from aaos.tasks.repository import TaskRepository


def record(
    *,
    task_id: str = "task-1",
    actor: str = "actor-1",
    key: str = "request-1",
    fingerprint: str = "a" * 64,
    state: TaskCreationState = TaskCreationState.CREATED,
    action_id: ActionId | None = None,
    failure: TaskFailureCode | None = None,
) -> TaskRecord:
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    return TaskRecord(
        task_id=task_id,
        task_kind=TaskKind.CALENDAR_CREATE_EVENT,
        actor_identity=ActorIdentity(actor),
        idempotency_key=key,
        request_fingerprint=fingerprint,
        creation_state=state,
        failure_code=failure,
        action_id=action_id,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repository(tmp_path):
    engine = create_database_engine(tmp_path / "task.db")
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(tmp_path / "task.db"))
    command.upgrade(config, "head")
    sessions = create_session_factory(engine)
    try:
        yield TaskRepository(sessions)
    finally:
        engine.dispose()


def test_create_or_get_is_idempotent_and_conflicts_on_fingerprint(repository: TaskRepository) -> None:
    first, created = repository.create_or_get(record())
    second, replayed = repository.create_or_get(record())

    assert created is True
    assert replayed is False
    assert second.task_id == first.task_id
    with pytest.raises(ValueError, match="idempotency"):
        repository.create_or_get(record(fingerprint="b" * 64))


def test_creation_state_cas_and_action_link_are_guarded(repository: TaskRepository) -> None:
    repository.create_or_get(record())
    with repository._session_factory() as session:  # type: ignore[attr-defined]
        session.execute(
            text(
                "INSERT INTO prepared_actions "
                "(action_id, operation_id, actor_identity, target_identity, action_type, "
                "canonical_arguments, arguments_sha256, tool_name, tool_schema_version, "
                "tool_definition_fingerprint, policy_version, policy_fingerprint, "
                "remote_precondition, prepared_at, action_expires_at, prepared_action_sha256) "
                "VALUES ('action-1', 'operation-1', 'actor-1', '{}', 'calendar.create', "
                ":arguments, :hash, 'calendar', 'v1', :hash, 'v1', :hash, '{}', "
                "'2026-09-18T10:00:00Z', '2026-09-18T11:00:00Z', :hash)"
            ),
            {"arguments": b'{}', "hash": "a" * 64},
        )
        session.commit()
    linked = repository.cas_creation_state(
        "task-1",
        expected=TaskCreationState.CREATED,
        target=TaskCreationState.ACTION_BOUND,
        action_id=ActionId("action-1"),
        failure_code=None,
        now=datetime(2026, 9, 18, 10, 1, tzinfo=timezone.utc),
    )
    assert linked.action_id == ActionId("action-1")
    with pytest.raises(ValueError, match="state"):
        repository.cas_creation_state(
            "task-1",
            expected=TaskCreationState.CREATED,
            target=TaskCreationState.FAILED,
            action_id=None,
            failure_code=TaskFailureCode.PREPARATION_FAILED.value,
            now=datetime(2026, 9, 18, 10, 2, tzinfo=timezone.utc),
        )


def test_list_is_deterministically_newest_first(repository: TaskRepository) -> None:
    repository.create_or_get(record(task_id="task-a", key="a"))
    repository.create_or_get(record(task_id="task-b", key="b"))
    assert [item.task_id for item in repository.list(limit=10)] == ["task-a", "task-b"]
