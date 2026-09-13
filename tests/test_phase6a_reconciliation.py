"""Task 8 tests for durable outcomes, recovery, and reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from test_phase6a_adapters import _claimed, _dispatch_context
from test_phase6a_service import NOW

from aaos.approval.adapters import FakeExternalAdapter, dispatch_claimed_operation
from aaos.approval.contracts import (
    ActionState,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    EventKind,
    ExecutionOutcome,
    OperationId,
    OperationState,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import (
    adapter_outcome_to_execution_outcome,
    outcome_to_states,
    reconcile_operation,
    record_adapter_outcome,
    recover_inflight_operation,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import ActionEventORM, OperationRecordORM
from aaos.persistence.database import create_database_engine, create_session_factory


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database_engine = create_database_engine(tmp_path / "phase6a-reconciliation.db")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> ApprovalRepository:
    return ApprovalRepository(sessions)


def _executing(repo: ApprovalRepository) -> OperationId:
    operation_id, precondition = _claimed(repo)

    class AcceptedAdapter(FakeExternalAdapter):
        def execute(self, action):  # type: ignore[no-untyped-def]
            return AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "accepted")

    result = dispatch_claimed_operation(
        repo,
        AcceptedAdapter(precondition),
        operation_id,
        **_dispatch_context(),
    )
    assert result.dispatch_started and result.adapter_invoked
    return operation_id


def test_successful_adapter_outcome_is_persisted_once(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    operation_id = _executing(repo)
    result = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "receipt-1"),
    )

    assert result.committed is True
    assert result.late is False
    assert result.reported_execution_outcome is ExecutionOutcome.CONFIRMED_SUCCESS
    assert result.persisted_execution_outcome is ExecutionOutcome.CONFIRMED_SUCCESS
    assert result.action_state is ActionState.CONFIRMED_SUCCESS
    assert result.operation_state is OperationState.CONFIRMED_SUCCESS
    with sessions() as session:
        operation = session.scalar(
            select(OperationRecordORM).where(OperationRecordORM.operation_id == operation_id.value)
        )
        assert operation is not None
        assert operation.outcome_kind == ExecutionOutcome.CONFIRMED_SUCCESS.value


def test_authoritative_failure_is_confirmed_failure(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    result = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.AUTHORITATIVE_FAILURE, "rejected"),
    )
    assert result.persisted_execution_outcome is ExecutionOutcome.CONFIRMED_FAILURE
    assert result.action_state is ActionState.CONFIRMED_FAILURE
    assert result.operation_state is OperationState.CONFIRMED_FAILURE


def test_unknown_adapter_outcome_is_not_confirmed_failure(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    result = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.TRANSPORT_FAILURE, "timeout"),
    )
    assert result.persisted_execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert result.action_state is ActionState.UNKNOWN_OUTCOME
    assert result.operation_state is OperationState.UNKNOWN_OUTCOME


def test_precondition_stale_has_no_execution_outcome(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    result = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.PRECONDITION_STALE, "stale"),
    )
    assert result.reported_execution_outcome is None
    assert result.persisted_execution_outcome is None
    assert result.action_state is ActionState.INVALIDATED
    assert result.operation_state is OperationState.NOT_DISPATCHED


def test_recovery_of_durable_executing_never_calls_execute(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    recovered = recover_inflight_operation(repo, operation_id, now=NOW + timedelta(minutes=1))
    assert recovered.outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert recovered.execute_calls == 0
    assert recovered.reconciliation_required is True


def test_reconciliation_only_calls_reconcile_and_can_confirm_success(
    repo: ApprovalRepository,
) -> None:
    operation_id = _executing(repo)
    record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.UNKNOWN, "uncertain"),
    )

    class ReconcilingAdapter(FakeExternalAdapter):
        def execute(self, action):  # type: ignore[no-untyped-def]
            raise AssertionError("recovery/reconciliation must not execute")

        def reconcile(self, operation):  # type: ignore[no-untyped-def]
            from aaos.approval.contracts import AdapterReconciliationOutcome

            return AdapterReconciliationOutcome(AdapterOutcomeKind.SUCCESS, "receipt-found")

    result = reconcile_operation(
        repo,
        ReconcilingAdapter(_claimed(repo)[1]),
        operation_id,
        expected_revision=2,
        now=NOW + timedelta(minutes=2),
    )
    assert result.outcome is ExecutionOutcome.CONFIRMED_SUCCESS
    assert result.execute_calls == 0
    assert result.committed is True


def test_reconciliation_unknown_remains_unknown_without_execute(
    repo: ApprovalRepository,
) -> None:
    operation_id = _executing(repo)
    record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.UNKNOWN, "uncertain"),
    )
    adapter = FakeExternalAdapter(_claimed(repo)[1])
    result = reconcile_operation(
        repo, adapter, operation_id, expected_revision=2, now=NOW + timedelta(minutes=2)
    )
    assert result.outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert result.execute_calls == 0
    assert adapter.execute_count == 0


def test_late_result_does_not_overwrite_persisted_truth(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    first = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "success"),
    )
    late = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.AUTHORITATIVE_FAILURE, "late"),
    )
    assert first.committed is True
    assert late.committed is False
    assert late.late is True
    assert late.reported_execution_outcome is ExecutionOutcome.CONFIRMED_FAILURE
    assert late.persisted_execution_outcome is ExecutionOutcome.CONFIRMED_SUCCESS
    assert late.action_state is ActionState.CONFIRMED_SUCCESS
    assert late.operation_state is OperationState.CONFIRMED_SUCCESS


def test_late_result_emits_sanitized_event(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    operation_id = _executing(repo)
    record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "success"),
    )
    record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.UNKNOWN, "late"),
    )
    with sessions() as session:
        events = session.scalars(
            select(ActionEventORM).where(
                ActionEventORM.event_kind == EventKind.LATE_RESULT_IGNORED.value
            )
        ).all()
    assert len(events) == 1


def test_no_automatic_retry_after_unknown(repo: ApprovalRepository) -> None:
    operation_id = _executing(repo)
    result = record_adapter_outcome(
        repo,
        operation_id,
        expected_revision=1,
        outcome=AdapterExecutionOutcome(AdapterOutcomeKind.UNKNOWN, "unknown"),
    )
    recovered = recover_inflight_operation(repo, operation_id, now=NOW + timedelta(minutes=1))
    assert result.committed is True
    assert recovered.execute_calls == 0


def test_closed_outcome_mappings() -> None:
    assert outcome_to_states(AdapterOutcomeKind.SUCCESS) == (
        ActionState.CONFIRMED_SUCCESS,
        OperationState.CONFIRMED_SUCCESS,
    )
    assert outcome_to_states(AdapterOutcomeKind.AUTHORITATIVE_FAILURE) == (
        ActionState.CONFIRMED_FAILURE,
        OperationState.CONFIRMED_FAILURE,
    )
    assert outcome_to_states(AdapterOutcomeKind.PRECONDITION_STALE) == (
        ActionState.INVALIDATED,
        OperationState.NOT_DISPATCHED,
    )
    assert (
        adapter_outcome_to_execution_outcome(
            AdapterExecutionOutcome(AdapterOutcomeKind.PRECONDITION_STALE, "stale")
        )
        is None
    )
