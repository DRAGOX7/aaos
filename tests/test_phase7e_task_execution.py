"""RED tests for the trusted Phase 7E execution composition."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_phase7e_task_service import ACTOR, APPROVER, NOW, _config, _definition, _request

from aaos.agent.contracts import PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import FakeExternalAdapter
from aaos.approval.contracts import (
    ActionType,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    AdapterReconciliationOutcome,
    ApprovalId,
    ExecutableAction,
    OperationId,
    Phase6ActionDefinition,
    RemotePrecondition,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import approve_approval
from aaos.integrations.google_calendar.contracts import calendar_target
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import OperationRecordORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.tasks.execution import TaskExecutionCoordinator
from aaos.tasks.read_service import TaskReadService
from aaos.tasks.repository import TaskRepository
from aaos.tasks.service import TaskApplicationService

@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(tmp_path / "phase7e-execution.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def test_execution_coordinator_accepts_only_trusted_phase6_dependencies(
    sessions: sessionmaker[Session],
) -> None:
    definition = Phase6ActionDefinition(
        action_type=ActionType("CREATE_EVENT"),
        tool_name="calendar_create_event",
        tool_schema_version="phase6b-calendar-v1",
        tool_definition_fingerprint="6" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase5-v1",
        policy_fingerprint="7" * 64,
        timeout_seconds=10,
        argument_validator=lambda arguments: arguments,
    )
    coordinator = TaskExecutionCoordinator(
        TaskRepository(sessions),
        ApprovalRepository(sessions),
        TaskReadService(sessions),
        adapter=FakeExternalAdapter(RemotePrecondition("fake", "resource", "v1", "etag")),
        definition=definition,
        capability_policy=PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
    )

    assert coordinator is not None


def _prepared_task(sessions: sessionmaker[Session]) -> tuple[TaskApplicationService, str]:
    service = TaskApplicationService(
        TaskRepository(sessions),
        ApprovalRepository(sessions),
        definition=_definition(),
        capability_policy=PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
        actor_identity=ACTOR,
        approver=APPROVER,
        calendar_config=_config(),
    )
    detail = service.create_calendar_task(_request(), idempotency_key="exec-1", now=NOW)
    assert detail.approval is not None
    approve_approval(
        ApprovalRepository(sessions),
        ApprovalId(detail.approval.approval_id),
        APPROVER,
        NOW,
    )
    return service, detail.task_id


def _coordinator(
    sessions: sessionmaker[Session],
    adapter: FakeExternalAdapter,
) -> TaskExecutionCoordinator:
    return TaskExecutionCoordinator(
        TaskRepository(sessions),
        ApprovalRepository(sessions),
        TaskReadService(sessions),
        adapter=adapter,
        definition=_definition(),
        capability_policy=PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
    )


def _adapter_precondition() -> RemotePrecondition:
    target = calendar_target(_config())
    return RemotePrecondition("google-calendar", target.resource, "phase6b-v1", "absent")


def test_success_dispatches_once_and_records_confirmed_success(
    sessions: sessionmaker[Session],
) -> None:
    _, task_id = _prepared_task(sessions)
    adapter = FakeExternalAdapter(_adapter_precondition())

    result = _coordinator(sessions, adapter).execute_approved_task(task_id, now=NOW)

    assert result.status.value == "CONFIRMED_SUCCESS"
    assert adapter.execute_count == 1
    assert adapter.side_effect_count == 1


def test_stale_precondition_is_not_a_side_effect_or_retry(
    sessions: sessionmaker[Session],
) -> None:
    _, task_id = _prepared_task(sessions)
    adapter = FakeExternalAdapter(_adapter_precondition())
    adapter.mutate_remote_state()

    result = _coordinator(sessions, adapter).execute_approved_task(task_id, now=NOW)

    assert result.status.value == "INVALIDATED"
    assert result.operation_state is not None
    assert result.operation_state.value == "NOT_DISPATCHED"
    assert adapter.execute_count == 1
    assert adapter.side_effect_count == 0


class UnknownAdapter(FakeExternalAdapter):
    def __init__(self, precondition: RemotePrecondition) -> None:
        super().__init__(precondition)
        self.reconcile_count = 0

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        self.execute_count += 1
        return AdapterExecutionOutcome(AdapterOutcomeKind.UNKNOWN, "transport uncertain")

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome:
        self.reconcile_count += 1
        return AdapterReconciliationOutcome(AdapterOutcomeKind.UNKNOWN, "still uncertain")


def test_unknown_outcome_reconciles_without_automatic_execute_retry(
    sessions: sessionmaker[Session],
) -> None:
    _, task_id = _prepared_task(sessions)
    adapter = UnknownAdapter(_adapter_precondition())

    result = _coordinator(sessions, adapter).execute_approved_task(task_id, now=NOW)

    assert result.status.value == "UNKNOWN_OUTCOME"
    assert adapter.execute_count == 1
    assert adapter.reconcile_count == 1


class DurableDispatchAdapter(FakeExternalAdapter):
    def __init__(self, precondition: RemotePrecondition, sessions: sessionmaker[Session]) -> None:
        super().__init__(precondition)
        self._sessions = sessions
        self.operation_state_at_entry: str | None = None

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        with self._sessions() as session:
            row = session.scalar(
                select(OperationRecordORM).where(
                    OperationRecordORM.operation_id == action.operation_id.value
                )
            )
            self.operation_state_at_entry = None if row is None else row.state
        return super().execute(action)


def test_dispatch_persists_executing_before_adapter_entry(
    sessions: sessionmaker[Session],
) -> None:
    _, task_id = _prepared_task(sessions)
    adapter = DurableDispatchAdapter(_adapter_precondition(), sessions)

    _coordinator(sessions, adapter).execute_approved_task(task_id, now=NOW)

    assert adapter.operation_state_at_entry == "EXECUTING"
