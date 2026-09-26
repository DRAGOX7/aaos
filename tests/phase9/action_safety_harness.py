"""Fake-only qualification harness for the frozen Phase 6 action boundary."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval import service
from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ActionType,
    ActorIdentity,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    AdapterReconciliationOutcome,
    ApprovalId,
    ApprovalRecord,
    ClaimIdentity,
    ExecutableAction,
    HumanDecisionReason,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    RemotePrecondition,
    TargetIdentity,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import (
    ActionEventORM,
    ActionLifecycleORM,
    ApprovalRecordORM,
    OperationRecordORM,
)
from aaos.persistence.database import create_database_engine, create_session_factory


class ActionScenario(StrEnum):
    SUCCESS = "SUCCESS"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    STALE_PRECONDITION = "STALE_PRECONDITION"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CAPABILITY_REVOKED = "CAPABILITY_REVOKED"


@dataclass(frozen=True, slots=True)
class FrozenClock:
    value: datetime = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)

    def now(self, seconds: int = 0) -> datetime:
        return self.value + timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class ActionScenarioResult:
    action_state: str
    operation_state: str
    execution_attempt_count: int
    reconciliation_count: int
    persisted_outcome: str | None
    event_kinds: tuple[str, ...]
    real_external_writes: int
    fake_side_effect_count: int = 0
    automatic_retry_count: int = 0
    model_calls: int = 0
    rag_calls: int = 0
    provider_payload: str | None = None
    secret_exposed: bool = False


@dataclass(frozen=True, slots=True)
class ClaimDispatchResult:
    claimed: bool
    operation_id: OperationId | None
    execution_attempt_count: int
    adapter_invoked: int
    dispatch_started: bool


class FakeExternalAdapter:
    """The only execution surface used by this harness; it has no provider."""

    def __init__(self, precondition: RemotePrecondition, outcome: AdapterOutcomeKind) -> None:
        self.precondition = precondition
        self.outcome = outcome
        self.execute_count = 0
        self.side_effect_count = 0
        self.last_action: ExecutableAction | None = None

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        self.execute_count += 1
        self.last_action = action
        if action.remote_precondition != self.precondition:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.PRECONDITION_STALE, "fake precondition stale"
            )
        if self.outcome is AdapterOutcomeKind.SUCCESS:
            self.side_effect_count += 1
        return AdapterExecutionOutcome(self.outcome, "bounded fake adapter evidence")

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome:
        del operation_id
        return AdapterReconciliationOutcome(
            AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
            "fake reconciliation confirmed failure",
        )


@contextmanager
def _repository() -> Iterator[tuple[ApprovalRepository, sessionmaker[Session]]]:
    with TemporaryDirectory(prefix="aaos-phase9-action-") as directory:
        engine = create_database_engine(Path(directory) / "phase9-action.db")
        Base.metadata.create_all(engine)
        sessions = create_session_factory(engine)
        try:
            yield ApprovalRepository(sessions), sessions
        finally:
            engine.dispose()


def _definition() -> Phase6ActionDefinition:
    return Phase6ActionDefinition(
        action_type=ActionType("PHASE9_FAKE_ACTION"),
        tool_name="phase9-fake-tool",
        tool_schema_version="phase9-v1",
        tool_definition_fingerprint="1" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase9-policy-v1",
        policy_fingerprint="2" * 64,
        timeout_seconds=10,
        argument_validator=lambda value: value,
    )


def _policy(allowed: bool = True) -> PermissionPolicy:
    return PermissionPolicy(
        granted_permissions=frozenset((PermissionClass.LOCAL_STATE_WRITE,) if allowed else ()),
        policy_version="phase9-policy-v1",
    )


def _prepare(
    repository: ApprovalRepository, clock: FrozenClock
) -> tuple[ActionId, RemotePrecondition]:
    definition = _definition()
    precondition = RemotePrecondition("phase9-fake", "resource", "v1", "etag")
    prepared = service.prepare_action(
        repository,
        definition,
        actor_identity=ActorIdentity("phase9-user"),
        target_identity=TargetIdentity("phase9-fake", "account", "resource"),
        arguments={"summary": "synthetic action"},
        remote_precondition=precondition,
        action_expires_at=clock.now(600),
        capability_policy=_policy(),
        now=clock.now(),
    )
    return prepared.action_id, precondition


def _approve(
    repository: ApprovalRepository,
    action_id: ActionId,
    clock: FrozenClock,
    *,
    expires_in: int = 300,
) -> None:
    approver = TrustedApproverContext("phase9-reviewer", "phase9-session")
    approval = service.request_approval(
        repository, action_id, approver, clock.now(expires_in), clock.now(1)
    )
    service.approve_approval(repository, approval.approval_id, approver, clock.now(2))


def _request(
    repository: ApprovalRepository,
    action_id: ActionId,
    clock: FrozenClock,
    *,
    expires_in: int = 300,
) -> ApprovalRecord:
    return service.request_approval(
        repository,
        action_id,
        TrustedApproverContext("phase9-reviewer", "phase9-session"),
        clock.now(expires_in),
        clock.now(1),
    )


def _approval_id(repository: ApprovalRepository, action_id: ActionId) -> ApprovalId:
    with repository.transaction() as session:
        row = session.scalar(
            select(ApprovalRecordORM).where(ApprovalRecordORM.action_id == action_id.value)
        )
        if row is None:
            raise AssertionError("approval missing")
        return ApprovalId(row.approval_id)


def _operation_snapshot(
    sessions: sessionmaker[Session], operation_id: OperationId
) -> tuple[int, ActionState, OperationState, str | None, tuple[str, ...]]:
    with sessions() as session:
        operation = session.get(OperationRecordORM, operation_id.value)
        if operation is None:
            raise AssertionError("operation missing")
        lifecycle = session.get(ActionLifecycleORM, operation.action_id)
        if lifecycle is None:
            raise AssertionError("lifecycle missing")
        events = tuple(
            row.event_kind
            for row in session.scalars(
                select(ActionEventORM)
                .where(ActionEventORM.action_id == operation.action_id)
                .order_by(ActionEventORM.sequence)
            )
        )
        return (
            operation.revision,
            ActionState(lifecycle.state),
            OperationState(operation.state),
            operation.outcome_kind,
            events,
        )


def run_action_scenario(
    scenario: ActionScenario | str, *, clock: FrozenClock
) -> ActionScenarioResult:
    selected = ActionScenario(scenario)
    with _repository() as (repository, sessions):
        action_id, precondition = _prepare(repository, clock)
        approver = TrustedApproverContext("phase9-reviewer", "phase9-session")
        if selected in {ActionScenario.REJECTED, ActionScenario.REVOKED}:
            approval = _request(repository, action_id, clock)
        else:
            approval = _request(
                repository,
                action_id,
                clock,
                expires_in=4 if selected is ActionScenario.EXPIRED else 300,
            )
            service.approve_approval(repository, approval.approval_id, approver, clock.now(2))
        if selected is ActionScenario.REJECTED:
            service.reject_approval(
                repository,
                approval.approval_id,
                approver,
                clock.now(3),
                HumanDecisionReason.USER_REJECTED,
            )
        elif selected is ActionScenario.REVOKED:
            service.revoke_approval(
                repository,
                approval.approval_id,
                approver,
                clock.now(3),
                HumanDecisionReason.USER_REVOKED,
            )
        else:
            # The expiry scenario intentionally claims after the approval TTL.
            pass

        claim_now = clock.now(3 if selected is not ActionScenario.EXPIRED else 5)
        policy = _policy(selected is not ActionScenario.CAPABILITY_REVOKED)
        claimed = service.claim_action(
            repository,
            action_id,
            capability_policy=policy,
            definition=_definition(),
            now=claim_now,
            claim_identity=ClaimIdentity("phase9-worker"),
        )
        if not claimed.claimed or claimed.operation_id is None:
            action_state, events = _read_terminal_without_operation(sessions, action_id)
            return ActionScenarioResult(
                action_state.value,
                "NONE",
                0,
                0,
                None,
                events,
                0,
            )

        operation_id = claimed.operation_id
        if selected is ActionScenario.STALE_PRECONDITION:
            adapter_precondition = RemotePrecondition("phase9-fake", "resource", "changed", "etag")
            outcome_kind = AdapterOutcomeKind.SUCCESS
        elif selected is ActionScenario.UNKNOWN_OUTCOME:
            adapter_precondition = precondition
            outcome_kind = AdapterOutcomeKind.UNKNOWN
        else:
            adapter_precondition = precondition
            outcome_kind = AdapterOutcomeKind.SUCCESS
        adapter = FakeExternalAdapter(adapter_precondition, outcome_kind)
        dispatch = service.dispatch_claimed_operation(
            repository,
            adapter,
            operation_id,
            capability_policy=_policy(),
            definition=_definition(),
            now=clock.now(4),
        )
        revision, _, operation_state, _, _ = _operation_snapshot(sessions, operation_id)
        if dispatch.adapter_outcome is not None and dispatch.adapter_invoked:
            service.record_adapter_outcome(
                repository,
                operation_id,
                expected_revision=revision,
                outcome=dispatch.adapter_outcome,
            )
        reconciliation_count = 0
        if selected is ActionScenario.UNKNOWN_OUTCOME:
            revision, _, operation_state, _, _ = _operation_snapshot(sessions, operation_id)
            reconciliation = service.reconcile_operation(
                repository,
                adapter,
                operation_id,
                expected_revision=revision,
                now=clock.now(5),
            )
            reconciliation_count = int(reconciliation.committed)
        _, action_state, operation_state, outcome, events = _operation_snapshot(
            sessions, operation_id
        )
        return ActionScenarioResult(
            action_state.value,
            operation_state.value,
            adapter.execute_count,
            reconciliation_count,
            outcome,
            events,
            0,
            adapter.side_effect_count,
        )


def _read_terminal_without_operation(
    sessions: sessionmaker[Session], action_id: ActionId
) -> tuple[ActionState, tuple[str, ...]]:
    with sessions() as session:
        lifecycle = session.get(ActionLifecycleORM, action_id.value)
        if lifecycle is None:
            raise AssertionError("lifecycle missing")
        events = tuple(
            row.event_kind
            for row in session.scalars(
                select(ActionEventORM)
                .where(ActionEventORM.action_id == action_id.value)
                .order_by(ActionEventORM.sequence)
            )
        )
        return ActionState(lifecycle.state), events


def run_concurrent_claims(
    *, clock: FrozenClock, dispatch: bool = True
) -> tuple[ClaimDispatchResult, ClaimDispatchResult]:
    # The production repository owns the CAS. Two workers are represented by
    # two sequential calls against the same trusted SQLite repository; the
    # second call observes the durable one-shot consumption fact.
    with _repository() as (repository, sessions):
        action_id, precondition = _prepare(repository, clock)
        _approve(repository, action_id, clock)
        results = []
        for _ in range(2):
            claimed = service.claim_action(
                repository,
                action_id,
                capability_policy=_policy(),
                definition=_definition(),
                now=clock.now(3),
                claim_identity=ClaimIdentity("phase9-worker"),
            )
            attempts = 0
            invoked = 0
            started = False
            if claimed.claimed and claimed.operation_id is not None and dispatch:
                adapter = FakeExternalAdapter(precondition, AdapterOutcomeKind.SUCCESS)
                outcome = service.dispatch_claimed_operation(
                    repository,
                    adapter,
                    claimed.operation_id,
                    capability_policy=_policy(),
                    definition=_definition(),
                    now=clock.now(4),
                )
                attempts = adapter.execute_count
                invoked = int(outcome.adapter_invoked)
                started = outcome.dispatch_started
            results.append(
                ClaimDispatchResult(
                    claimed.claimed,
                    claimed.operation_id,
                    attempts,
                    invoked,
                    started,
                )
            )
        return results[0], results[1]


__all__ = [
    "ActionScenario",
    "ActionScenarioResult",
    "ClaimDispatchResult",
    "FakeExternalAdapter",
    "FrozenClock",
    "run_action_scenario",
    "run_concurrent_claims",
]
