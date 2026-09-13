"""Phase 6A fake-adapter boundary and durable dispatch fence.

This module deliberately contains no real provider integration.  The durable
dispatch marker is committed before an adapter is given the least-authority
``ExecutableAction`` projection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.agent.policy import PermissionPolicy
from aaos.approval.contracts import (
    ActionId,
    ActionState,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    AdapterReconciliationOutcome,
    ApprovalId,
    ClaimIdentity,
    EventKind,
    ExecutableAction,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    Phase6ErrorCode,
    PreparedAction,
    RemotePrecondition,
    project_executable_action,
)
from aaos.approval.events import ActionEventDraft, DispatchStartedDetails
from aaos.approval.repository import (
    ApprovalRepository,
    PersistenceConflict,
    cas_action_lifecycle,
)
from aaos.approval.service import (
    _approval_from_orm,
    _capability_projection,
    _prepared_from_orm,
)
from aaos.approval.state import (
    DispatchFenceSnapshot,
    FenceDecision,
    TransitionSource,
    evaluate_dispatch_fence,
    validate_operation_transition,
)
from aaos.persistence.approval_models import (
    ActionLifecycleORM,
    ApprovalConsumptionORM,
    ApprovalRecordORM,
    OperationRecordORM,
    PreparedActionORM,
)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Result of one durable dispatch attempt (outcome persistence is Task 8)."""

    operation_id: OperationId
    adapter_outcome: AdapterExecutionOutcome | None
    adapter_invoked: bool
    dispatch_started: bool
    error_code: Phase6ErrorCode | None


class ExternalAdapter(Protocol):
    """The only execution interface available to Phase 6A dispatch."""

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome: ...

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome: ...


class FakeExternalAdapter:
    """Deterministic in-memory adapter used by Phase 6A acceptance tests."""

    def __init__(self, remote_precondition: RemotePrecondition) -> None:
        if not isinstance(remote_precondition, RemotePrecondition):
            raise TypeError("remote_precondition must be a RemotePrecondition")
        self._remote_precondition = remote_precondition
        self._lock = Lock()
        self.side_effect_count = 0
        self.execute_count = 0
        self.last_action: ExecutableAction | None = None

    def check_precondition(self, action: ExecutableAction) -> bool:
        if not isinstance(action, ExecutableAction):
            raise TypeError("action must be an ExecutableAction")
        with self._lock:
            return action.remote_precondition == self._remote_precondition

    def mutate_remote_state(self) -> None:
        """Change the fake remote version without touching an external system."""

        with self._lock:
            self._remote_precondition = RemotePrecondition(
                self._remote_precondition.provider,
                self._remote_precondition.resource,
                self._remote_precondition.version + "-changed",
                self._remote_precondition.etag + "-changed",
            )

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        if not isinstance(action, ExecutableAction):
            raise TypeError("action must be an ExecutableAction")
        with self._lock:
            self.execute_count += 1
            self.last_action = action
            # This check and the fake side-effect counter are one critical
            # section: preliminary check_precondition() is advisory only.
            if action.remote_precondition != self._remote_precondition:
                return AdapterExecutionOutcome(
                    AdapterOutcomeKind.PRECONDITION_STALE,
                    "remote precondition was stale before the side effect",
                )
            self.side_effect_count += 1
            return AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "fake side effect recorded")

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome:
        if not isinstance(operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId")
        return AdapterReconciliationOutcome(
            AdapterOutcomeKind.UNKNOWN,
            "fake adapter has no external reconciliation evidence",
        )


@dataclass(frozen=True, slots=True)
class _DispatchRows:
    prepared: PreparedAction
    lifecycle: ActionLifecycleORM
    approval: ApprovalRecordORM
    consumption: ApprovalConsumptionORM
    operation: OperationRecordORM
    stored_precondition: str


def _precondition_from_storage(value: str) -> RemotePrecondition:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != {
            "provider",
            "resource",
            "version",
            "etag",
        }:
            raise ValueError
        return RemotePrecondition(
            parsed["provider"], parsed["resource"], parsed["version"], parsed["etag"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise PersistenceConflict() from None


def _load_dispatch_rows(session: Session, operation_id: OperationId) -> _DispatchRows:
    try:
        operation = session.get(OperationRecordORM, operation_id.value)
        if operation is None:
            raise PersistenceConflict()
        prepared_row = session.get(PreparedActionORM, operation.action_id)
        lifecycle = session.get(ActionLifecycleORM, operation.action_id)
        approval = session.scalar(
            select(ApprovalRecordORM).where(ApprovalRecordORM.action_id == operation.action_id)
        )
        consumption = session.scalar(
            select(ApprovalConsumptionORM).where(
                ApprovalConsumptionORM.operation_id == operation_id.value
            )
        )
    except PersistenceConflict:
        raise
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if prepared_row is None or lifecycle is None or approval is None or consumption is None:
        raise PersistenceConflict()
    prepared = _prepared_from_orm(prepared_row)
    if operation.action_id != prepared.action_id.value or operation_id != prepared.operation_id:
        raise PersistenceConflict()
    return _DispatchRows(
        prepared, lifecycle, approval, consumption, operation, prepared_row.remote_precondition
    )


def _snapshot_from_rows(
    rows: _DispatchRows,
    stored_precondition: RemotePrecondition,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
) -> DispatchFenceSnapshot:
    prepared = rows.prepared
    approval = _approval_from_orm(rows.approval, prepared)
    binding_matches = (
        approval.action_id == prepared.action_id
        and approval.operation_id == prepared.operation_id
        and rows.consumption.approval_id == approval.approval_id.value
        and rows.consumption.action_id == prepared.action_id.value
        and rows.consumption.operation_id == prepared.operation_id.value
        and approval.prepared_action_sha256 == prepared.prepared_action_sha256
        and prepared.action_type == definition.action_type
        and prepared.tool_name == definition.tool_name
        and prepared.tool_schema_version == definition.tool_schema_version
        and prepared.tool_definition_fingerprint == definition.tool_definition_fingerprint
        and prepared.policy_version == definition.policy_version
        and prepared.policy_fingerprint == definition.policy_fingerprint
    )
    try:
        capability = capability_policy.evaluate(_capability_projection(definition))
        capability_allowed = (
            capability.allowed and capability.required_permission is definition.required_permission
        )
        lifecycle_state = ActionState(rows.lifecycle.state)
        operation_state = OperationState(rows.operation.state)
        approval_state = approval.status
    except (TypeError, ValueError):
        capability_allowed = False
        lifecycle_state = ActionState.CLAIMED
        operation_state = OperationState.CLAIMED
        approval_state = approval.status
    return DispatchFenceSnapshot(
        action_id=prepared.action_id,
        operation_id=prepared.operation_id,
        lifecycle_state=lifecycle_state,
        operation_state=operation_state,
        approval_state=approval_state,
        consumed_approval_id=ApprovalId(rows.consumption.approval_id),
        consumed_action_id=ActionId(rows.consumption.action_id),
        consumed_operation_id=OperationId(rows.consumption.operation_id),
        capability_allowed=capability_allowed,
        binding_matches=binding_matches,
        stored_precondition_intact=stored_precondition == prepared.remote_precondition,
        action_expires_at=prepared.action_expires_at,
        approval_expires_at=approval.approval_expires_at,
        evaluated_at=now,
        dispatch_started_at=rows.operation.dispatch_started_at,
        operation_revision=rows.operation.revision,
    )


def persist_dispatch_fence_failure(
    session: Session,
    operation_id: OperationId,
    decision: FenceDecision,
) -> None:
    """Validate a fence rejection without performing Task 8 outcome writes.

    Task 7 intentionally leaves invalidation/outcome persistence to Task 8;
    this function is the explicit boundary for that deferred behavior.
    """

    if not isinstance(session, Session) or not isinstance(operation_id, OperationId):
        raise TypeError("invalid dispatch fence failure values")
    if not isinstance(decision, FenceDecision):
        raise TypeError("decision must be a FenceDecision")
    # Task 8 owns durable invalidation and NOT_DISPATCHED mapping.  In
    # particular, a fence rejection must not manufacture an execution result.
    del decision


def load_executable_action(
    repository: ApprovalRepository,
    operation_id: OperationId,
) -> ExecutableAction:
    if not isinstance(repository, ApprovalRepository) or not isinstance(operation_id, OperationId):
        raise TypeError("repository and operation_id must be trusted values")
    with repository.transaction() as session:
        try:
            operation = session.get(OperationRecordORM, operation_id.value)
            if operation is None:
                raise PersistenceConflict()
            prepared_row = session.get(PreparedActionORM, operation.action_id)
            if prepared_row is None:
                raise PersistenceConflict()
            prepared = _prepared_from_orm(prepared_row)
            if prepared.operation_id != operation_id:
                raise PersistenceConflict()
        except PersistenceConflict:
            raise
        except SQLAlchemyError:
            raise PersistenceConflict() from None
    return project_executable_action(prepared)


def dispatch_claimed_operation(
    repository: ApprovalRepository,
    adapter: ExternalAdapter,
    operation_id: OperationId,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
) -> DispatchResult:
    """Fence and start exactly one claimed operation before adapter I/O."""

    if not isinstance(repository, ApprovalRepository) or not isinstance(operation_id, OperationId):
        raise TypeError("repository and operation_id must be trusted values")
    if not isinstance(capability_policy, PermissionPolicy):
        raise TypeError("capability_policy must be a PermissionPolicy")
    if not isinstance(definition, Phase6ActionDefinition):
        raise TypeError("definition must be a Phase6ActionDefinition")
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() != timezone.utc.utcoffset(now)
    ):
        raise ValueError("now must be an aware UTC datetime")
    if not hasattr(adapter, "execute"):
        raise TypeError("adapter must provide execute")

    try:
        with repository.transaction() as session:
            rows = _load_dispatch_rows(session, operation_id)
            stored_precondition = _precondition_from_storage(rows.stored_precondition)
            snapshot = _snapshot_from_rows(
                rows,
                stored_precondition,
                capability_policy=capability_policy,
                definition=definition,
                now=now,
            )
            decision = evaluate_dispatch_fence(snapshot)
            if not decision.allowed:
                persist_dispatch_fence_failure(session, operation_id, decision)
                return DispatchResult(operation_id, None, False, False, decision.error_code)

            validate_operation_transition(
                OperationState.CLAIMED,
                OperationState.EXECUTING,
                TransitionSource.DISPATCH_WORKER,
            )
            operation_result = cast(
                CursorResult[Any],
                session.execute(
                    update(OperationRecordORM)
                    .where(
                        OperationRecordORM.operation_id == operation_id.value,
                        OperationRecordORM.state == OperationState.CLAIMED.value,
                        OperationRecordORM.revision == rows.operation.revision,
                    )
                    .values(
                        state=OperationState.EXECUTING.value,
                        revision=rows.operation.revision + 1,
                        dispatch_started_at=now,
                    )
                ),
            )
            if operation_result.rowcount != 1:
                return DispatchResult(
                    operation_id, None, False, False, Phase6ErrorCode.CLAIM_CONFLICT
                )
            event = ActionEventDraft(
                action_id=rows.prepared.action_id,
                operation_id=operation_id,
                event_kind=EventKind.DISPATCH_STARTED,
                source=TransitionSource.DISPATCH_WORKER,
                actor=ClaimIdentity(rows.consumption.claim_identity),
                occurred_at=now,
                details=DispatchStartedDetails(operation_id, now),
            )
            if not cas_action_lifecycle(
                session,
                action_id=rows.prepared.action_id,
                expected_state=ActionState.CLAIMED,
                expected_revision=rows.lifecycle.revision,
                new_state=ActionState.EXECUTING,
                event=event,
            ):
                raise PersistenceConflict()
        # The repository transaction is committed here.  In particular, no
        # adapter check or execute call occurs while SQLite is locked.
        executable = load_executable_action(repository, operation_id)
        try:
            outcome = adapter.execute(executable)
        except Exception:
            outcome = AdapterExecutionOutcome(
                AdapterOutcomeKind.UNKNOWN,
                "adapter outcome unavailable",
            )
        if not isinstance(outcome, AdapterExecutionOutcome):
            outcome = AdapterExecutionOutcome(
                AdapterOutcomeKind.UNKNOWN, "adapter outcome unavailable"
            )
        return DispatchResult(operation_id, outcome, True, True, None)
    except PersistenceConflict:
        return DispatchResult(operation_id, None, False, False, Phase6ErrorCode.CLAIM_CONFLICT)
    except (SQLAlchemyError, TypeError, ValueError):
        return DispatchResult(
            operation_id, None, False, False, Phase6ErrorCode.PERSISTENCE_CONFLICT
        )


__all__ = [
    "DispatchResult",
    "ExternalAdapter",
    "FakeExternalAdapter",
    "dispatch_claimed_operation",
    "load_executable_action",
    "persist_dispatch_fence_failure",
]
