"""Trusted preparation and human approval transitions for Phase 6A."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.agent.contracts import JSONValue, PermissionClass, ToolName
from aaos.agent.policy import PermissionPolicy
from aaos.agent.registry import SideEffectClass, ToolDefinition, ToolInputSchema
from aaos.approval.contracts import (
    ActionId,
    ActionRecord,
    ActionState,
    ActionType,
    ActorIdentity,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    ApprovalDecision,
    ApprovalId,
    ApprovalRecord,
    ApprovalState,
    ClaimIdentity,
    EventKind,
    ExecutableAction,
    ExecutionOutcome,
    HumanDecisionReason,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    Phase6ErrorCode,
    PreparedAction,
    PreparedActionBinding,
    RemotePrecondition,
    TargetIdentity,
    TrustedApproverContext,
    TrustedInvalidationReason,
    build_prepared_action,
    compute_approval_binding_sha256,
    compute_prepared_action_sha256,
)
from aaos.approval.events import (
    ActionEventDraft,
    ActionPreparedDetails,
    ApprovalDecisionDetails,
    ApprovalRequestedDetails,
    ClaimedDetails,
    InvalidatedDetails,
    LateResultIgnoredDetails,
    OutcomeRecordedDetails,
    ReconciliationDetails,
)
from aaos.approval.repository import (
    ApprovalRepository,
    PersistenceConflict,
    append_action_event,
    cas_action_lifecycle,
)
from aaos.approval.state import (
    TransitionSource,
    validate_approval_transition,
    validate_operation_transition,
)
from aaos.persistence.approval_models import (
    ActionLifecycleORM,
    ApprovalConsumptionORM,
    ApprovalRecordORM,
    OperationRecordORM,
    PreparedActionORM,
)

if TYPE_CHECKING:
    from aaos.approval.adapters import DispatchResult, ExternalAdapter

_SYSTEM_ACTOR = ActorIdentity("phase6a-trusted-system")
_CAPABILITY_TOOL = {
    PermissionClass.READ_ONLY: ToolName.READ_TASK_STATE,
    PermissionClass.LOCAL_STATE_WRITE: ToolName.WRITE_TASK_STATE,
    PermissionClass.TERMINAL: ToolName.COMPLETE_TASK,
}
_SIDE_EFFECT = {
    PermissionClass.READ_ONLY: SideEffectClass.READ_ONLY,
    PermissionClass.LOCAL_STATE_WRITE: SideEffectClass.LOCAL_STATE_WRITE,
    PermissionClass.TERMINAL: SideEffectClass.TERMINAL,
}
_DRIFT_REASONS = frozenset(
    {
        TrustedInvalidationReason.CAPABILITY_REVOKED,
        TrustedInvalidationReason.SCHEMA_VERSION_DRIFT,
        TrustedInvalidationReason.POLICY_VERSION_DRIFT,
        TrustedInvalidationReason.BINDING_DRIFT,
        TrustedInvalidationReason.STORED_PRECONDITION_DRIFT,
    }
)


class Phase6ServiceError(RuntimeError):
    """Stable service rejection containing no database or untrusted details."""

    def __init__(self, code: Phase6ErrorCode) -> None:
        if not isinstance(code, Phase6ErrorCode):
            raise TypeError("code must be a Phase6ErrorCode")
        self.code = code
        super().__init__(code.value)


def _raise(code: Phase6ErrorCode) -> NoReturn:
    raise Phase6ServiceError(code)


@dataclass(frozen=True, slots=True)
class ClaimSnapshot:
    """Database-only values used to authorize one approval claim."""

    action: PreparedAction
    approval: ApprovalRecord
    stored_precondition: RemotePrecondition
    lifecycle_state: ActionState
    lifecycle_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.action, PreparedAction):
            raise TypeError("action must be a PreparedAction")
        if not isinstance(self.approval, ApprovalRecord):
            raise TypeError("approval must be an ApprovalRecord")
        if not isinstance(self.stored_precondition, RemotePrecondition):
            raise TypeError("stored_precondition must be a RemotePrecondition")
        if not isinstance(self.lifecycle_state, ActionState):
            raise TypeError("lifecycle_state must be an ActionState")
        if (
            isinstance(self.lifecycle_revision, bool)
            or not isinstance(self.lifecycle_revision, int)
            or self.lifecycle_revision < 0
        ):
            raise ValueError("lifecycle_revision must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    invalidates: bool
    error_code: Phase6ErrorCode | None

    def __post_init__(self) -> None:
        if not isinstance(self.invalidates, bool):
            raise TypeError("invalidates must be a bool")
        if self.error_code is not None and not isinstance(self.error_code, Phase6ErrorCode):
            raise TypeError("error_code must be a Phase6ErrorCode or None")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    action_id: ActionId
    operation_id: OperationId | None
    claimed: bool
    error_code: Phase6ErrorCode | None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, ActionId):
            raise TypeError("action_id must be an ActionId")
        if self.operation_id is not None and not isinstance(self.operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId or None")
        if not isinstance(self.claimed, bool):
            raise TypeError("claimed must be a bool")
        if self.error_code is not None and not isinstance(self.error_code, Phase6ErrorCode):
            raise TypeError("error_code must be a Phase6ErrorCode or None")


@dataclass(frozen=True, slots=True)
class OutcomeRecordResult:
    operation_id: OperationId
    committed: bool
    late: bool
    reported_execution_outcome: ExecutionOutcome | None
    persisted_execution_outcome: ExecutionOutcome | None
    action_state: ActionState
    operation_state: OperationState
    error_code: Phase6ErrorCode | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    operation_id: OperationId
    outcome: ExecutionOutcome | None
    execute_calls: int
    committed: bool


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    operation_id: OperationId
    outcome: ExecutionOutcome | None
    execute_calls: int
    reconciliation_required: bool


class _OutcomeCASLost(Exception):
    """Internal marker used to reopen a transaction for a late-result event."""


class _ReconciliationCASLost(Exception):
    """Internal marker used to reopen a transaction for a late reconcile event."""


def _utc(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{name} must be an aware UTC datetime")
    return value


def _capability_projection(definition: Phase6ActionDefinition) -> ToolDefinition:
    """Project a Phase 6A definition onto the frozen Phase 5 policy input type."""

    permission = definition.required_permission
    try:
        name = ToolName(definition.tool_name)
    except ValueError:
        name = _CAPABILITY_TOOL[permission]
    return ToolDefinition(
        name=name,
        description="Trusted Phase 6A capability projection",
        input_schema=ToolInputSchema.empty("phase6a_capability"),
        required_permission=permission,
        side_effect=_SIDE_EFFECT[permission],
        timeout_seconds=definition.timeout_seconds,
    )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except SQLAlchemyError:
        raise PersistenceConflict() from None


def _json_text(value: Mapping[str, str]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prepared_orm(prepared: PreparedAction) -> PreparedActionORM:
    return PreparedActionORM(
        action_id=prepared.action_id.value,
        operation_id=prepared.operation_id.value,
        actor_identity=prepared.actor_identity.value,
        target_identity=_json_text(prepared.target_identity.to_dict()),
        action_type=prepared.action_type.value,
        canonical_arguments=prepared.canonical_arguments,
        arguments_sha256=prepared.arguments_sha256,
        tool_name=prepared.tool_name,
        tool_schema_version=prepared.tool_schema_version,
        tool_definition_fingerprint=prepared.tool_definition_fingerprint,
        policy_version=prepared.policy_version,
        policy_fingerprint=prepared.policy_fingerprint,
        remote_precondition=_json_text(prepared.remote_precondition.to_dict()),
        prepared_at=prepared.prepared_at,
        action_expires_at=prepared.action_expires_at,
        prepared_action_sha256=prepared.prepared_action_sha256,
    )


def _mapping(value: str, name: str) -> Mapping[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise PersistenceConflict() from None
    if not isinstance(parsed, dict):
        raise PersistenceConflict()
    if any(not isinstance(key, str) for key in parsed):
        raise PersistenceConflict()
    return parsed


def _prepared_from_orm(row: PreparedActionORM) -> PreparedAction:
    target = _mapping(row.target_identity, "target_identity")
    precondition = _mapping(row.remote_precondition, "remote_precondition")
    try:
        return PreparedAction(
            action_id=ActionId(row.action_id),
            operation_id=OperationId(row.operation_id),
            actor_identity=ActorIdentity(row.actor_identity),
            target_identity=TargetIdentity.from_dict(target),
            action_type=ActionType(row.action_type),
            canonical_arguments=row.canonical_arguments,
            arguments_sha256=row.arguments_sha256,
            tool_name=row.tool_name,
            tool_schema_version=row.tool_schema_version,
            tool_definition_fingerprint=row.tool_definition_fingerprint,
            policy_version=row.policy_version,
            policy_fingerprint=row.policy_fingerprint,
            remote_precondition=RemotePrecondition(
                cast(str, precondition.get("provider")),
                cast(str, precondition.get("resource")),
                cast(str, precondition.get("version")),
                cast(str, precondition.get("etag")),
            ),
            prepared_at=row.prepared_at,
            action_expires_at=row.action_expires_at,
            prepared_action_sha256=row.prepared_action_sha256,
        )
    except (TypeError, ValueError):
        raise PersistenceConflict() from None


def _approval_from_orm(row: ApprovalRecordORM, prepared: PreparedAction) -> ApprovalRecord:
    reason: HumanDecisionReason | TrustedInvalidationReason | None = None
    if row.reason_code is not None:
        try:
            reason = HumanDecisionReason(row.reason_code)
        except ValueError:
            try:
                reason = TrustedInvalidationReason(row.reason_code)
            except ValueError:
                raise PersistenceConflict() from None
    try:
        return ApprovalRecord(
            approval_id=ApprovalId(row.approval_id),
            action_id=ActionId(row.action_id),
            operation_id=OperationId(row.operation_id),
            approver=TrustedApproverContext(row.approver_identity, row.approver_context_id),
            prepared_action_sha256=row.prepared_action_sha256,
            approval_binding_sha256=row.approval_binding_sha256,
            issued_at=row.issued_at,
            approval_expires_at=row.approval_expires_at,
            action_expires_at=prepared.action_expires_at,
            status=ApprovalState(row.status),
            decided_at=row.decided_at,
            reason=reason,
        )
    except (TypeError, ValueError):
        raise PersistenceConflict() from None


def _claim_invalidated(reason: TrustedInvalidationReason) -> ClaimDecision:
    # The public result stays at the closed ACTION_INVALIDATED code; the
    # durable approval reason carries the more specific trusted cause.
    return ClaimDecision(True, Phase6ErrorCode.ACTION_INVALIDATED)


def validate_claim_snapshot(
    snapshot: ClaimSnapshot,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
) -> ClaimDecision:
    """Purely validate the durable claim boundary; never perform live I/O."""

    if not isinstance(snapshot, ClaimSnapshot):
        raise TypeError("snapshot must be a ClaimSnapshot")
    if not isinstance(capability_policy, PermissionPolicy):
        raise TypeError("capability_policy must be a PermissionPolicy")
    if not isinstance(definition, Phase6ActionDefinition):
        raise TypeError("definition must be a Phase6ActionDefinition")
    now = _utc(now, "now")

    # Terminal human decisions are authoritative and are not rewritten by a
    # claim worker, even when their deadline has also elapsed.
    approval_state = snapshot.approval.status
    if approval_state is ApprovalState.REVOKED:
        return ClaimDecision(False, Phase6ErrorCode.APPROVAL_REVOKED)
    if approval_state is ApprovalState.REJECTED:
        return ClaimDecision(False, Phase6ErrorCode.HUMAN_REJECTED)
    if approval_state is ApprovalState.CONSUMED:
        return ClaimDecision(False, Phase6ErrorCode.APPROVAL_ALREADY_CONSUMED)
    if approval_state is ApprovalState.EXPIRED:
        return ClaimDecision(False, Phase6ErrorCode.APPROVAL_EXPIRED)
    if approval_state is not ApprovalState.APPROVED:
        return ClaimDecision(False, Phase6ErrorCode.INVALID_STATE_TRANSITION)

    if snapshot.lifecycle_state is not ActionState.APPROVED:
        return ClaimDecision(False, Phase6ErrorCode.INVALID_STATE_TRANSITION)
    if snapshot.action.action_expires_at <= now or snapshot.approval.approval_expires_at <= now:
        return ClaimDecision(True, Phase6ErrorCode.APPROVAL_EXPIRED)

    action = snapshot.action
    approval = snapshot.approval
    if (
        approval.action_id != action.action_id
        or approval.operation_id != action.operation_id
        or approval.prepared_action_sha256 != action.prepared_action_sha256
    ):
        return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)
    try:
        if (
            compute_approval_binding_sha256(
                approval.prepared_action_sha256, approval.approver, approval.approval_expires_at
            )
            != approval.approval_binding_sha256
        ):
            return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)
        if compute_prepared_action_sha256(action) != action.prepared_action_sha256:
            return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)
    except (TypeError, ValueError):
        return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)

    if action.tool_schema_version != definition.tool_schema_version:
        return _claim_invalidated(TrustedInvalidationReason.SCHEMA_VERSION_DRIFT)
    if (
        action.policy_version != definition.policy_version
        or action.policy_fingerprint != definition.policy_fingerprint
        or capability_policy.policy_version != action.policy_version
    ):
        return _claim_invalidated(TrustedInvalidationReason.POLICY_VERSION_DRIFT)
    if (
        action.action_type != definition.action_type
        or action.tool_name != definition.tool_name
        or action.tool_definition_fingerprint != definition.tool_definition_fingerprint
    ):
        return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)
    if snapshot.stored_precondition != action.remote_precondition:
        return _claim_invalidated(TrustedInvalidationReason.STORED_PRECONDITION_DRIFT)

    capability = capability_policy.evaluate(_capability_projection(definition))
    if (
        not capability.allowed
        or capability.required_permission is not definition.required_permission
    ):
        return ClaimDecision(True, Phase6ErrorCode.CAPABILITY_DENIED)
    try:
        arguments = json.loads(action.canonical_arguments.decode("utf-8"))
        canonical = definition.validate_arguments(arguments)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return _claim_invalidated(TrustedInvalidationReason.SCHEMA_VERSION_DRIFT)
    if canonical.bytes != action.canonical_arguments or canonical.sha256 != action.arguments_sha256:
        return _claim_invalidated(TrustedInvalidationReason.BINDING_DRIFT)
    return ClaimDecision(False, None)


def _load_action(
    session: Session, action_id: ActionId
) -> tuple[PreparedAction, ActionLifecycleORM]:
    try:
        row = session.get(PreparedActionORM, action_id.value)
        lifecycle = session.get(ActionLifecycleORM, action_id.value)
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if row is None or lifecycle is None:
        _raise(Phase6ErrorCode.INVALID_PREPARED_ACTION)
    return _prepared_from_orm(row), lifecycle


def _load_approval(
    session: Session, approval_id: ApprovalId
) -> tuple[ApprovalRecord, PreparedAction, ActionLifecycleORM, ApprovalRecordORM]:
    try:
        row = session.get(ApprovalRecordORM, approval_id.value)
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if row is None:
        _raise(Phase6ErrorCode.APPROVAL_NOT_FOUND)
    prepared, lifecycle = _load_action(session, ActionId(row.action_id))
    approval = _approval_from_orm(row, prepared)
    if approval.prepared_action_sha256 != prepared.prepared_action_sha256:
        _raise(Phase6ErrorCode.VERSION_MISMATCH)
    return approval, prepared, lifecycle, row


def _cas_approval(
    session: Session,
    *,
    approval_id: ApprovalId,
    expected_state: ApprovalState,
    new_state: ApprovalState,
    decided_at: datetime,
    reason: HumanDecisionReason | TrustedInvalidationReason,
    source: TransitionSource,
) -> None:
    validate_approval_transition(expected_state, new_state, source)
    try:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ApprovalRecordORM)
                .where(
                    ApprovalRecordORM.approval_id == approval_id.value,
                    ApprovalRecordORM.status == expected_state.value,
                )
                .values(
                    status=new_state.value,
                    decided_at=decided_at,
                    reason_code=reason.value,
                )
            ),
        )
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if result.rowcount != 1:
        raise PersistenceConflict()


def prepare_action(
    repository: ApprovalRepository,
    definition: Phase6ActionDefinition,
    *,
    actor_identity: ActorIdentity,
    target_identity: TargetIdentity,
    arguments: Mapping[str, JSONValue],
    remote_precondition: RemotePrecondition,
    action_expires_at: datetime,
    capability_policy: PermissionPolicy,
    now: datetime,
) -> PreparedAction:
    if not isinstance(repository, ApprovalRepository):
        raise TypeError("repository must be an ApprovalRepository")
    if not isinstance(definition, Phase6ActionDefinition):
        raise TypeError("definition must be a Phase6ActionDefinition")
    if not isinstance(capability_policy, PermissionPolicy):
        raise TypeError("capability_policy must be a PermissionPolicy")
    decision = capability_policy.evaluate(_capability_projection(definition))
    if not decision.allowed or decision.required_permission is not definition.required_permission:
        _raise(Phase6ErrorCode.CAPABILITY_DENIED)
    validated = definition.validate_arguments(arguments)
    binding = PreparedActionBinding(
        action_id=ActionId(uuid4().hex),
        operation_id=OperationId(uuid4().hex),
        actor_identity=actor_identity,
        target_identity=target_identity,
        action_type=definition.action_type,
        canonical_arguments=validated.bytes,
        arguments_sha256=validated.sha256,
        tool_name=definition.tool_name,
        tool_schema_version=definition.tool_schema_version,
        tool_definition_fingerprint=definition.tool_definition_fingerprint,
        policy_version=definition.policy_version,
        policy_fingerprint=definition.policy_fingerprint,
        remote_precondition=remote_precondition,
        prepared_at=_utc(now, "now"),
        action_expires_at=_utc(action_expires_at, "action_expires_at"),
    )
    prepared = build_prepared_action(binding)
    with repository.transaction() as session:
        session.add(_prepared_orm(prepared))
        _flush(session)
        session.add(
            ActionLifecycleORM(
                action_id=prepared.action_id.value,
                state=ActionState.PREPARED.value,
                revision=0,
                created_at=now,
                updated_at=now,
                terminal_at=None,
            )
        )
        _flush(session)
        append_action_event(
            session,
            ActionEventDraft(
                action_id=prepared.action_id,
                operation_id=prepared.operation_id,
                event_kind=EventKind.ACTION_PREPARED,
                source=TransitionSource.TRUSTED_SYSTEM,
                actor=actor_identity,
                occurred_at=now,
                details=ActionPreparedDetails(
                    operation_id=prepared.operation_id,
                    prepared_action_sha256=prepared.prepared_action_sha256,
                ),
            ),
            expected_action_revision=None,
        )
    return prepared


def request_approval(
    repository: ApprovalRepository,
    action_id: ActionId,
    approver: TrustedApproverContext,
    approval_expires_at: datetime,
    now: datetime,
) -> ApprovalRecord:
    if not isinstance(repository, ApprovalRepository) or not isinstance(action_id, ActionId):
        raise TypeError("invalid trusted request values")
    if not isinstance(approver, TrustedApproverContext):
        raise TypeError("approver must be a TrustedApproverContext")
    now = _utc(now, "now")
    approval_expires_at = _utc(approval_expires_at, "approval_expires_at")
    with repository.transaction() as session:
        prepared, lifecycle = _load_action(session, action_id)
        if ActionState(lifecycle.state) is not ActionState.PREPARED:
            _raise(Phase6ErrorCode.INVALID_STATE_TRANSITION)
        if (
            prepared.action_expires_at <= now
            or approval_expires_at <= now
            or approval_expires_at > prepared.action_expires_at
        ):
            _raise(Phase6ErrorCode.APPROVAL_EXPIRED)
        approval_id = ApprovalId(uuid4().hex)
        binding_sha256 = compute_approval_binding_sha256(
            prepared.prepared_action_sha256, approver, approval_expires_at
        )
        approval = ApprovalRecord(
            approval_id=approval_id,
            action_id=prepared.action_id,
            operation_id=prepared.operation_id,
            approver=approver,
            prepared_action_sha256=prepared.prepared_action_sha256,
            approval_binding_sha256=binding_sha256,
            issued_at=now,
            approval_expires_at=approval_expires_at,
            action_expires_at=prepared.action_expires_at,
            status=ApprovalState.PENDING,
        )
        session.add(
            ApprovalRecordORM(
                approval_id=approval.approval_id.value,
                action_id=approval.action_id.value,
                operation_id=approval.operation_id.value,
                approver_identity=approval.approver.approver_identity.value,
                approver_context_id=approval.approver.context_id,
                prepared_action_sha256=approval.prepared_action_sha256,
                approval_binding_sha256=approval.approval_binding_sha256,
                issued_at=approval.issued_at,
                approval_expires_at=approval.approval_expires_at,
                status=approval.status.value,
                decided_at=None,
                reason_code=None,
            )
        )
        _flush(session)
        event = ActionEventDraft(
            action_id=prepared.action_id,
            operation_id=prepared.operation_id,
            event_kind=EventKind.APPROVAL_REQUESTED,
            source=TransitionSource.TRUSTED_SYSTEM,
            actor=prepared.actor_identity,
            occurred_at=now,
            details=ApprovalRequestedDetails(
                approval_id=approval.approval_id,
                approval_binding_sha256=approval.approval_binding_sha256,
                approval_expires_at=approval.approval_expires_at,
            ),
        )
        if not cas_action_lifecycle(
            session,
            action_id=prepared.action_id,
            expected_state=ActionState.PREPARED,
            expected_revision=lifecycle.revision,
            new_state=ActionState.AWAITING_APPROVAL,
            event=event,
        ):
            raise PersistenceConflict()
    return approval


def _human_decision(
    repository: ApprovalRepository,
    decision: ApprovalDecision,
    *,
    expected_states: frozenset[ApprovalState],
    new_approval_state: ApprovalState,
    new_action_state: ActionState,
) -> ApprovalRecord:
    if not isinstance(repository, ApprovalRepository):
        raise TypeError("repository must be an ApprovalRepository")
    if not isinstance(decision, ApprovalDecision):
        raise TypeError("decision must be an ApprovalDecision")
    with repository.transaction() as session:
        approval, prepared, lifecycle, row = _load_approval(session, decision.approval_id)
        if approval.approver != decision.approver:
            _raise(Phase6ErrorCode.APPROVAL_NOT_FOUND)
        if approval.status not in expected_states:
            _raise(Phase6ErrorCode.INVALID_STATE_TRANSITION)
        if (
            approval.approval_expires_at <= decision.decided_at
            or prepared.action_expires_at <= decision.decided_at
        ):
            _raise(Phase6ErrorCode.APPROVAL_EXPIRED)
        lifecycle_state = ActionState(lifecycle.state)
        expected_action_state = (
            ActionState.AWAITING_APPROVAL
            if approval.status is ApprovalState.PENDING
            else ActionState.APPROVED
        )
        if lifecycle_state is not expected_action_state:
            _raise(Phase6ErrorCode.INVALID_STATE_TRANSITION)
        _cas_approval(
            session,
            approval_id=approval.approval_id,
            expected_state=approval.status,
            new_state=new_approval_state,
            decided_at=decision.decided_at,
            reason=decision.reason,
            source=TransitionSource.HUMAN_APPROVER,
        )
        event = ActionEventDraft(
            action_id=prepared.action_id,
            operation_id=prepared.operation_id,
            event_kind=EventKind.APPROVAL_DECIDED,
            source=TransitionSource.HUMAN_APPROVER,
            actor=decision.approver,
            occurred_at=decision.decided_at,
            details=ApprovalDecisionDetails(
                approval_id=approval.approval_id,
                reason=decision.reason,
                decided_at=decision.decided_at,
            ),
        )
        if not cas_action_lifecycle(
            session,
            action_id=prepared.action_id,
            expected_state=expected_action_state,
            expected_revision=lifecycle.revision,
            new_state=new_action_state,
            event=event,
        ):
            raise PersistenceConflict()
        row.status = new_approval_state.value
        approval.status = new_approval_state
        approval.decided_at = decision.decided_at
        approval.reason = decision.reason
    return approval


def approve_approval(
    repository: ApprovalRepository,
    approval_id: ApprovalId,
    approver: TrustedApproverContext,
    now: datetime,
) -> ApprovalRecord:
    return _human_decision(
        repository,
        ApprovalDecision(approval_id, approver, HumanDecisionReason.USER_APPROVED, now),
        expected_states=frozenset({ApprovalState.PENDING}),
        new_approval_state=ApprovalState.APPROVED,
        new_action_state=ActionState.APPROVED,
    )


def reject_approval(
    repository: ApprovalRepository,
    approval_id: ApprovalId,
    approver: TrustedApproverContext,
    now: datetime,
    reason: HumanDecisionReason,
) -> ApprovalRecord:
    if not isinstance(reason, HumanDecisionReason):
        raise TypeError("reason must be a HumanDecisionReason")
    if reason is not HumanDecisionReason.USER_REJECTED:
        raise ValueError("reject_approval requires USER_REJECTED")
    return _human_decision(
        repository,
        ApprovalDecision(approval_id, approver, reason, now),
        expected_states=frozenset({ApprovalState.PENDING}),
        new_approval_state=ApprovalState.REJECTED,
        new_action_state=ActionState.REJECTED,
    )


def revoke_approval(
    repository: ApprovalRepository,
    approval_id: ApprovalId,
    approver: TrustedApproverContext,
    now: datetime,
    reason: HumanDecisionReason,
) -> ApprovalRecord:
    if not isinstance(reason, HumanDecisionReason):
        raise TypeError("reason must be a HumanDecisionReason")
    if reason is not HumanDecisionReason.USER_REVOKED:
        raise ValueError("revoke_approval requires USER_REVOKED")
    return _human_decision(
        repository,
        ApprovalDecision(approval_id, approver, reason, now),
        expected_states=frozenset({ApprovalState.PENDING, ApprovalState.APPROVED}),
        new_approval_state=ApprovalState.REVOKED,
        new_action_state=ActionState.REVOKED,
    )


def invalidate_for_trusted_drift(
    repository: ApprovalRepository,
    action_id: ActionId,
    reason: TrustedInvalidationReason,
    now: datetime,
) -> ActionRecord:
    if not isinstance(repository, ApprovalRepository) or not isinstance(action_id, ActionId):
        raise TypeError("invalid trusted invalidation values")
    if not isinstance(reason, TrustedInvalidationReason):
        raise TypeError("reason must be a TrustedInvalidationReason")
    if reason not in _DRIFT_REASONS:
        raise ValueError("reason must describe trusted binding drift")
    now = _utc(now, "now")
    with repository.transaction() as session:
        prepared, lifecycle = _load_action(session, action_id)
        if ActionState(lifecycle.state) is not ActionState.APPROVED:
            _raise(Phase6ErrorCode.INVALID_STATE_TRANSITION)
        try:
            row = session.scalar(
                select(ApprovalRecordORM).where(ApprovalRecordORM.action_id == action_id.value)
            )
        except SQLAlchemyError:
            raise PersistenceConflict() from None
        if row is None:
            _raise(Phase6ErrorCode.APPROVAL_NOT_FOUND)
        approval = _approval_from_orm(row, prepared)
        if approval.status is not ApprovalState.APPROVED:
            _raise(Phase6ErrorCode.INVALID_STATE_TRANSITION)
        _cas_approval(
            session,
            approval_id=approval.approval_id,
            expected_state=ApprovalState.APPROVED,
            new_state=ApprovalState.REVOKED,
            decided_at=now,
            reason=reason,
            source=TransitionSource.TRUSTED_SYSTEM,
        )
        event = ActionEventDraft(
            action_id=prepared.action_id,
            operation_id=prepared.operation_id,
            event_kind=EventKind.ACTION_INVALIDATED,
            source=TransitionSource.TRUSTED_SYSTEM,
            actor=_SYSTEM_ACTOR,
            occurred_at=now,
            details=InvalidatedDetails(reason),
        )
        if not cas_action_lifecycle(
            session,
            action_id=prepared.action_id,
            expected_state=ActionState.APPROVED,
            expected_revision=lifecycle.revision,
            new_state=ActionState.INVALIDATED,
            event=event,
        ):
            raise PersistenceConflict()
        approval.status = ApprovalState.REVOKED
        approval.decided_at = now
        approval.reason = reason
    return ActionRecord(prepared, ActionState.INVALIDATED, approval=approval)


def _claim_reason(
    snapshot: ClaimSnapshot,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    decision: ClaimDecision,
) -> TrustedInvalidationReason:
    if decision.error_code is Phase6ErrorCode.APPROVAL_EXPIRED:
        return TrustedInvalidationReason.ACTION_EXPIRED
    if decision.error_code is Phase6ErrorCode.CAPABILITY_DENIED:
        return TrustedInvalidationReason.CAPABILITY_REVOKED
    action = snapshot.action
    approval = snapshot.approval
    if action.tool_schema_version != definition.tool_schema_version or (
        action.policy_version != definition.policy_version
        or action.policy_fingerprint != definition.policy_fingerprint
        or capability_policy.policy_version != action.policy_version
    ):
        return (
            TrustedInvalidationReason.SCHEMA_VERSION_DRIFT
            if action.tool_schema_version != definition.tool_schema_version
            else TrustedInvalidationReason.POLICY_VERSION_DRIFT
        )
    if snapshot.stored_precondition != action.remote_precondition:
        return TrustedInvalidationReason.STORED_PRECONDITION_DRIFT
    if (
        approval.action_id != action.action_id
        or approval.operation_id != action.operation_id
        or approval.prepared_action_sha256 != action.prepared_action_sha256
    ):
        return TrustedInvalidationReason.BINDING_DRIFT
    # A definition whose validator no longer accepts the immutable canonical
    # arguments is schema drift.  Keep this distinction when translating the
    # public ClaimDecision into the durable trusted reason.
    try:
        arguments = json.loads(action.canonical_arguments.decode("utf-8"))
        canonical = definition.validate_arguments(arguments)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return TrustedInvalidationReason.SCHEMA_VERSION_DRIFT
    if canonical.bytes != action.canonical_arguments or canonical.sha256 != action.arguments_sha256:
        return TrustedInvalidationReason.BINDING_DRIFT
    return TrustedInvalidationReason.BINDING_DRIFT


def _invalidate_claim(
    session: Session,
    snapshot: ClaimSnapshot,
    *,
    reason: TrustedInvalidationReason,
    now: datetime,
) -> None:
    target = (
        ApprovalState.EXPIRED
        if reason is TrustedInvalidationReason.ACTION_EXPIRED
        else ApprovalState.REVOKED
    )
    action_target = (
        ActionState.EXPIRED
        if reason is TrustedInvalidationReason.ACTION_EXPIRED
        else ActionState.INVALIDATED
    )
    _cas_approval(
        session,
        approval_id=snapshot.approval.approval_id,
        expected_state=ApprovalState.APPROVED,
        new_state=target,
        decided_at=now,
        reason=reason,
        source=TransitionSource.TRUSTED_SYSTEM,
    )
    event = ActionEventDraft(
        action_id=snapshot.action.action_id,
        operation_id=None,
        event_kind=EventKind.ACTION_INVALIDATED,
        source=TransitionSource.TRUSTED_SYSTEM,
        actor=_SYSTEM_ACTOR,
        occurred_at=now,
        details=InvalidatedDetails(reason),
    )
    if not cas_action_lifecycle(
        session,
        action_id=snapshot.action.action_id,
        expected_state=ActionState.APPROVED,
        expected_revision=snapshot.lifecycle_revision,
        new_state=action_target,
        event=event,
    ):
        raise PersistenceConflict()


def _classify_claim_conflict(
    repository: ApprovalRepository, action_id: ActionId
) -> Phase6ErrorCode:
    """Distinguish a one-shot loser from an untouched persistence failure."""

    try:
        with repository.transaction() as session:
            lifecycle = session.get(ActionLifecycleORM, action_id.value)
            consumed = session.scalar(
                select(ApprovalRecordORM).where(ApprovalRecordORM.action_id == action_id.value)
            )
            operation = session.scalar(
                select(OperationRecordORM).where(OperationRecordORM.action_id == action_id.value)
            )
            if lifecycle is not None and (
                lifecycle.state != ActionState.APPROVED.value
                or (consumed is not None and consumed.status == ApprovalState.CONSUMED.value)
                or operation is not None
            ):
                return Phase6ErrorCode.CLAIM_CONFLICT
    except (PersistenceConflict, SQLAlchemyError):
        return Phase6ErrorCode.PERSISTENCE_CONFLICT
    return Phase6ErrorCode.PERSISTENCE_CONFLICT


def claim_action(
    repository: ApprovalRepository,
    action_id: ActionId,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
    claim_identity: ClaimIdentity,
) -> ClaimResult:
    """Atomically consume an approved action without dispatching it."""

    if not isinstance(repository, ApprovalRepository) or not isinstance(action_id, ActionId):
        raise TypeError("repository and action_id must be trusted values")
    if not isinstance(capability_policy, PermissionPolicy):
        raise TypeError("capability_policy must be a PermissionPolicy")
    if not isinstance(definition, Phase6ActionDefinition):
        raise TypeError("definition must be a Phase6ActionDefinition")
    if not isinstance(claim_identity, ClaimIdentity):
        raise TypeError("claim_identity must be a ClaimIdentity")
    now = _utc(now, "now")

    try:
        with repository.transaction() as session:
            snapshot = repository.load_claim_snapshot(session, action_id)
            decision = validate_claim_snapshot(
                snapshot,
                capability_policy=capability_policy,
                definition=definition,
                now=now,
            )
            if decision.invalidates:
                _invalidate_claim(
                    session,
                    snapshot,
                    reason=_claim_reason(
                        snapshot,
                        capability_policy=capability_policy,
                        definition=definition,
                        decision=decision,
                    ),
                    now=now,
                )
                return ClaimResult(action_id, None, False, decision.error_code)
            if decision.error_code is not None:
                return ClaimResult(action_id, None, False, decision.error_code)

            approval_id = snapshot.approval.approval_id
            operation_id = snapshot.action.operation_id
            event = ActionEventDraft(
                action_id=action_id,
                operation_id=operation_id,
                event_kind=EventKind.APPROVAL_CONSUMED,
                source=TransitionSource.CLAIM_WORKER,
                actor=claim_identity,
                occurred_at=now,
                details=ClaimedDetails(
                    approval_id=approval_id,
                    operation_id=operation_id,
                    claim_identity=claim_identity,
                ),
            )
            repository.insert_consumption(
                session,
                approval_id=approval_id,
                action_id=action_id,
                operation_id=operation_id,
                claim_identity=claim_identity,
                consumed_at=now,
            )
            repository.mark_approval_consumed(
                session,
                approval_id=approval_id,
                expected_status=ApprovalState.APPROVED,
                consumed_at=now,
            )
            repository.cas_claimed_and_create_operation(
                session,
                action_id=action_id,
                operation_id=operation_id,
                expected_state=ActionState.APPROVED,
                expected_revision=snapshot.lifecycle_revision,
                claim_identity=claim_identity,
                now=now,
            )
            repository.append_claim_event(
                session,
                event=event,
                expected_action_revision=snapshot.lifecycle_revision,
            )
            return ClaimResult(action_id, operation_id, True, None)
    except PersistenceConflict:
        return ClaimResult(action_id, None, False, _classify_claim_conflict(repository, action_id))


def outcome_to_states(
    kind: AdapterOutcomeKind,
) -> tuple[ActionState, OperationState]:
    """Map a closed adapter outcome to the corresponding durable states."""

    if not isinstance(kind, AdapterOutcomeKind):
        raise TypeError("kind must be an AdapterOutcomeKind")
    if kind is AdapterOutcomeKind.SUCCESS:
        return ActionState.CONFIRMED_SUCCESS, OperationState.CONFIRMED_SUCCESS
    if kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE:
        return ActionState.CONFIRMED_FAILURE, OperationState.CONFIRMED_FAILURE
    if kind is AdapterOutcomeKind.PRECONDITION_STALE:
        return ActionState.INVALIDATED, OperationState.NOT_DISPATCHED
    return ActionState.UNKNOWN_OUTCOME, OperationState.UNKNOWN_OUTCOME


def adapter_outcome_to_execution_outcome(
    outcome: AdapterExecutionOutcome,
) -> ExecutionOutcome | None:
    """Map adapter evidence without inventing an outcome for stale dispatch."""

    if not isinstance(outcome, AdapterExecutionOutcome):
        raise TypeError("outcome must be an AdapterExecutionOutcome")
    if outcome.kind is AdapterOutcomeKind.SUCCESS:
        return ExecutionOutcome.CONFIRMED_SUCCESS
    if outcome.kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE:
        return ExecutionOutcome.CONFIRMED_FAILURE
    if outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE:
        return None
    return ExecutionOutcome.UNKNOWN_OUTCOME


def _outcome_now() -> datetime:
    return datetime.now(timezone.utc)


def _operation_rows(
    session: Session, operation_id: OperationId
) -> tuple[OperationRecordORM, ActionLifecycleORM, ApprovalConsumptionORM]:
    try:
        operation = session.get(OperationRecordORM, operation_id.value)
        if operation is None:
            raise PersistenceConflict()
        lifecycle = session.get(ActionLifecycleORM, operation.action_id)
        consumption = session.scalar(
            select(ApprovalConsumptionORM).where(
                ApprovalConsumptionORM.operation_id == operation_id.value
            )
        )
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if lifecycle is None or consumption is None:
        raise PersistenceConflict()
    return operation, lifecycle, consumption


def _safe_operation_rows(
    session: Session, operation_id: OperationId
) -> tuple[OperationRecordORM, ActionLifecycleORM, ApprovalConsumptionORM]:
    try:
        return _operation_rows(session, operation_id)
    except SQLAlchemyError:
        raise PersistenceConflict() from None


def _safe_outcome(value: str | None) -> ExecutionOutcome | None:
    if value is None:
        return None
    try:
        return ExecutionOutcome(value)
    except ValueError:
        raise PersistenceConflict() from None


def _late_outcome_result(
    session: Session,
    operation_id: OperationId,
    outcome: AdapterExecutionOutcome,
) -> OutcomeRecordResult:
    operation, lifecycle, consumption = _safe_operation_rows(session, operation_id)
    try:
        action_state = ActionState(lifecycle.state)
        operation_state = OperationState(operation.state)
    except ValueError:
        raise PersistenceConflict() from None
    reported = adapter_outcome_to_execution_outcome(outcome)
    event = ActionEventDraft(
        action_id=ActionId(operation.action_id),
        operation_id=operation_id,
        event_kind=EventKind.LATE_RESULT_IGNORED,
        source=TransitionSource.DISPATCH_WORKER,
        actor=ClaimIdentity(consumption.claim_identity),
        occurred_at=_outcome_now(),
        details=LateResultIgnoredDetails(
            operation_id,
            reported,
            Phase6ErrorCode.PERSISTENCE_CONFLICT,
        ),
    )
    append_action_event(session, event, expected_action_revision=None)
    return OutcomeRecordResult(
        operation_id,
        committed=False,
        late=True,
        reported_execution_outcome=reported,
        persisted_execution_outcome=_safe_outcome(operation.outcome_kind),
        action_state=action_state,
        operation_state=operation_state,
        error_code=Phase6ErrorCode.PERSISTENCE_CONFLICT,
    )


def record_adapter_outcome(
    repository: ApprovalRepository,
    operation_id: OperationId,
    *,
    expected_revision: int,
    outcome: AdapterExecutionOutcome,
) -> OutcomeRecordResult:
    """Persist one adapter result using the executing-operation CAS."""

    if not isinstance(repository, ApprovalRepository) or not isinstance(operation_id, OperationId):
        raise TypeError("repository and operation_id must be trusted values")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")
    if not isinstance(outcome, AdapterExecutionOutcome):
        raise TypeError("outcome must be an AdapterExecutionOutcome")
    reported = adapter_outcome_to_execution_outcome(outcome)
    now = _outcome_now()
    try:
        with repository.transaction() as session:
            operation, lifecycle, consumption = _safe_operation_rows(session, operation_id)
            if (
                operation.state != OperationState.EXECUTING.value
                or operation.revision != expected_revision
                or lifecycle.state != ActionState.EXECUTING.value
            ):
                return _late_outcome_result(session, operation_id, outcome)
            action_id = ActionId(operation.action_id)
            claim_identity = ClaimIdentity(consumption.claim_identity)
            action_target, operation_target = outcome_to_states(outcome.kind)
            if outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE:
                event = ActionEventDraft(
                    action_id=action_id,
                    operation_id=operation_id,
                    event_kind=EventKind.ACTION_INVALIDATED,
                    source=TransitionSource.DISPATCH_WORKER,
                    actor=claim_identity,
                    occurred_at=now,
                    details=InvalidatedDetails(Phase6ErrorCode.PRECONDITION_STALE),
                )
            else:
                event = ActionEventDraft(
                    action_id=action_id,
                    operation_id=operation_id,
                    event_kind=EventKind.OUTCOME_RECORDED,
                    source=TransitionSource.DISPATCH_WORKER,
                    actor=claim_identity,
                    occurred_at=now,
                    details=OutcomeRecordedDetails(
                        operation_id,
                        reported,
                        now,
                        None
                        if reported is not ExecutionOutcome.UNKNOWN_OUTCOME
                        else Phase6ErrorCode.UNKNOWN_OUTCOME,
                    ),
                )
            if not cas_action_lifecycle(
                session,
                action_id=action_id,
                expected_state=ActionState(lifecycle.state),
                expected_revision=lifecycle.revision,
                new_state=action_target,
                event=event,
            ):
                raise _OutcomeCASLost()
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(OperationRecordORM)
                    .where(
                        OperationRecordORM.operation_id == operation_id.value,
                        OperationRecordORM.state == OperationState.EXECUTING.value,
                        OperationRecordORM.revision == expected_revision,
                    )
                    .values(
                        state=operation_target.value,
                        outcome_kind=reported.value if reported is not None else None,
                        receipt_evidence=outcome.evidence,
                        outcome_recorded_at=now,
                        revision=expected_revision + 1,
                    )
                ),
            )
            if result.rowcount != 1:
                raise _OutcomeCASLost()
            return OutcomeRecordResult(
                operation_id,
                committed=True,
                late=False,
                reported_execution_outcome=reported,
                persisted_execution_outcome=reported,
                action_state=action_target,
                operation_state=operation_target,
                error_code=(
                    Phase6ErrorCode.PRECONDITION_STALE
                    if outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE
                    else None
                ),
            )
    except _OutcomeCASLost:
        with repository.transaction() as session:
            return _late_outcome_result(session, operation_id, outcome)
    except SQLAlchemyError:
        raise PersistenceConflict() from None


def _persist_reconciliation(
    repository: ApprovalRepository,
    operation_id: OperationId,
    *,
    expected_revision: int,
    outcome: AdapterExecutionOutcome,
    now: datetime,
) -> ReconciliationResult:
    outcome_value = adapter_outcome_to_execution_outcome(outcome)
    if outcome_value is None:
        outcome_value = ExecutionOutcome.UNKNOWN_OUTCOME
    try:
        with repository.transaction() as session:
            operation, lifecycle, consumption = _safe_operation_rows(session, operation_id)
            try:
                action_state = ActionState(lifecycle.state)
                operation_state = OperationState(operation.state)
            except ValueError:
                raise PersistenceConflict() from None
            if (
                operation_state is not OperationState.RECONCILIATION_PENDING
                or operation.revision != expected_revision
            ):
                event = ActionEventDraft(
                    action_id=ActionId(operation.action_id),
                    operation_id=operation_id,
                    event_kind=EventKind.LATE_RESULT_IGNORED,
                    source=TransitionSource.RECONCILIATION_WORKER,
                    actor=ClaimIdentity(consumption.claim_identity),
                    occurred_at=now,
                    details=LateResultIgnoredDetails(
                        operation_id, outcome_value, Phase6ErrorCode.PERSISTENCE_CONFLICT
                    ),
                )
                append_action_event(session, event, expected_action_revision=None)
                return ReconciliationResult(
                    operation_id,
                    _safe_outcome(operation.outcome_kind)
                    if operation_state is not OperationState.NOT_DISPATCHED
                    else None,
                    0,
                    False,
                )
            action_target, operation_target = outcome_to_states(
                AdapterOutcomeKind.SUCCESS
                if outcome_value is ExecutionOutcome.CONFIRMED_SUCCESS
                else AdapterOutcomeKind.AUTHORITATIVE_FAILURE
                if outcome_value is ExecutionOutcome.CONFIRMED_FAILURE
                else AdapterOutcomeKind.UNKNOWN
            )
            event = ActionEventDraft(
                action_id=ActionId(operation.action_id),
                operation_id=operation_id,
                event_kind=EventKind.RECONCILIATION_RECORDED,
                source=TransitionSource.RECONCILIATION_WORKER,
                actor=ClaimIdentity(consumption.claim_identity),
                occurred_at=now,
                details=ReconciliationDetails(operation_id, outcome_value, now),
            )
            if not cas_action_lifecycle(
                session,
                action_id=ActionId(operation.action_id),
                expected_state=action_state,
                expected_revision=lifecycle.revision,
                new_state=action_target,
                event=event,
            ):
                raise _ReconciliationCASLost()
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(OperationRecordORM)
                    .where(
                        OperationRecordORM.operation_id == operation_id.value,
                        OperationRecordORM.state == OperationState.RECONCILIATION_PENDING.value,
                        OperationRecordORM.revision == expected_revision,
                    )
                    .values(
                        state=operation_target.value,
                        outcome_kind=outcome_value.value,
                        receipt_evidence=outcome.evidence,
                        outcome_recorded_at=now,
                        reconciliation_at=now,
                        revision=expected_revision + 1,
                    )
                ),
            )
            if result.rowcount != 1:
                raise _ReconciliationCASLost()
            return ReconciliationResult(operation_id, outcome_value, 0, True)
    except _ReconciliationCASLost:
        return _late_reconciliation_result(repository, operation_id, outcome_value, now)
    except SQLAlchemyError:
        raise PersistenceConflict() from None


def _late_reconciliation_result(
    repository: ApprovalRepository,
    operation_id: OperationId,
    reported: ExecutionOutcome,
    now: datetime,
) -> ReconciliationResult:
    with repository.transaction() as session:
        operation, _, consumption = _safe_operation_rows(session, operation_id)
        operation_state = OperationState(operation.state)
        persisted = _safe_outcome(operation.outcome_kind)
        event = ActionEventDraft(
            action_id=ActionId(operation.action_id),
            operation_id=operation_id,
            event_kind=EventKind.LATE_RESULT_IGNORED,
            source=TransitionSource.RECONCILIATION_WORKER,
            actor=ClaimIdentity(consumption.claim_identity),
            occurred_at=now,
            details=LateResultIgnoredDetails(
                operation_id, reported, Phase6ErrorCode.PERSISTENCE_CONFLICT
            ),
        )
        append_action_event(session, event, expected_action_revision=None)
        return ReconciliationResult(
            operation_id,
            None if operation_state is OperationState.NOT_DISPATCHED else persisted or reported,
            0,
            False,
        )


def reconcile_operation(
    repository: ApprovalRepository,
    adapter: ExternalAdapter,
    operation_id: OperationId,
    *,
    expected_revision: int,
    now: datetime,
) -> ReconciliationResult:
    """Reconcile only an uncertain operation; never call adapter.execute."""

    if not isinstance(repository, ApprovalRepository) or not isinstance(operation_id, OperationId):
        raise TypeError("repository and operation_id must be trusted values")
    if not hasattr(adapter, "reconcile"):
        raise TypeError("adapter must provide reconcile")
    now = _utc(now, "now")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")
    pending_revision = expected_revision
    with repository.transaction() as session:
        operation, lifecycle, consumption = _safe_operation_rows(session, operation_id)
        operation_state = OperationState(operation.state)
        action_state = ActionState(lifecycle.state)
        if operation_state is OperationState.UNKNOWN_OUTCOME:
            validate_operation_transition(
                operation_state,
                OperationState.RECONCILIATION_PENDING,
                TransitionSource.RECONCILIATION_WORKER,
            )
            event = ActionEventDraft(
                action_id=ActionId(operation.action_id),
                operation_id=operation_id,
                event_kind=EventKind.OUTCOME_RECORDED,
                source=TransitionSource.RECONCILIATION_WORKER,
                actor=ClaimIdentity(consumption.claim_identity),
                occurred_at=now,
                details=OutcomeRecordedDetails(
                    operation_id, None, now, Phase6ErrorCode.RECONCILIATION_REQUIRED
                ),
            )
            if not cas_action_lifecycle(
                session,
                action_id=ActionId(operation.action_id),
                expected_state=action_state,
                expected_revision=lifecycle.revision,
                new_state=ActionState.RECONCILIATION_PENDING,
                event=event,
            ):
                raise PersistenceConflict()
            try:
                updated = cast(
                    CursorResult[Any],
                    session.execute(
                        update(OperationRecordORM)
                        .where(
                            OperationRecordORM.operation_id == operation_id.value,
                            OperationRecordORM.state == OperationState.UNKNOWN_OUTCOME.value,
                            OperationRecordORM.revision == expected_revision,
                        )
                        .values(
                            state=OperationState.RECONCILIATION_PENDING.value,
                            revision=expected_revision + 1,
                            reconciliation_at=now,
                        )
                    ),
                )
            except SQLAlchemyError:
                raise PersistenceConflict() from None
            if updated.rowcount != 1:
                raise PersistenceConflict()
            pending_revision += 1
        elif operation_state is not OperationState.RECONCILIATION_PENDING:
            persisted = _safe_outcome(operation.outcome_kind)
            return ReconciliationResult(operation_id, persisted, 0, False)
        elif operation.revision != expected_revision:
            return ReconciliationResult(
                operation_id,
                _safe_outcome(operation.outcome_kind),
                0,
                False,
            )

    adapter_outcome: AdapterExecutionOutcome
    from aaos.approval.adapters import AdapterExecutionError

    try:
        adapter_outcome = adapter.reconcile(operation_id)
        if not isinstance(adapter_outcome, AdapterExecutionOutcome):
            raise TypeError("adapter returned an invalid typed reconciliation outcome")
    except (AdapterExecutionError, TimeoutError, ConnectionError):
        adapter_outcome = AdapterExecutionOutcome(
            AdapterOutcomeKind.UNKNOWN, "reconciliation unavailable"
        )
    return _persist_reconciliation(
        repository,
        operation_id,
        expected_revision=pending_revision,
        outcome=adapter_outcome,
        now=now,
    )


def recover_inflight_operation(
    repository: ApprovalRepository,
    operation_id: OperationId,
    *,
    now: datetime,
) -> RecoveryResult:
    """Recover durable in-flight state without invoking an adapter."""

    if not isinstance(repository, ApprovalRepository) or not isinstance(operation_id, OperationId):
        raise TypeError("repository and operation_id must be trusted values")
    now = _utc(now, "now")
    with repository.transaction() as session:
        operation, lifecycle, consumption = _safe_operation_rows(session, operation_id)
        operation_state = OperationState(operation.state)
        action_state = ActionState(lifecycle.state)
        persisted = _safe_outcome(operation.outcome_kind)
        if operation_state is not OperationState.EXECUTING:
            return RecoveryResult(
                operation_id,
                persisted,
                0,
                operation_state
                in {OperationState.UNKNOWN_OUTCOME, OperationState.RECONCILIATION_PENDING},
            )
        event = ActionEventDraft(
            action_id=ActionId(operation.action_id),
            operation_id=operation_id,
            event_kind=EventKind.OUTCOME_RECORDED,
            source=TransitionSource.DISPATCH_WORKER,
            actor=ClaimIdentity(consumption.claim_identity),
            occurred_at=now,
            details=OutcomeRecordedDetails(
                operation_id, ExecutionOutcome.UNKNOWN_OUTCOME, now, Phase6ErrorCode.UNKNOWN_OUTCOME
            ),
        )
        if not cas_action_lifecycle(
            session,
            action_id=ActionId(operation.action_id),
            expected_state=action_state,
            expected_revision=lifecycle.revision,
            new_state=ActionState.UNKNOWN_OUTCOME,
            event=event,
        ):
            raise PersistenceConflict()
        try:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(OperationRecordORM)
                    .where(
                        OperationRecordORM.operation_id == operation_id.value,
                        OperationRecordORM.state == OperationState.EXECUTING.value,
                        OperationRecordORM.revision == operation.revision,
                    )
                    .values(
                        state=OperationState.UNKNOWN_OUTCOME.value,
                        outcome_kind=ExecutionOutcome.UNKNOWN_OUTCOME.value,
                        outcome_recorded_at=now,
                        revision=operation.revision + 1,
                    )
                ),
            )
        except SQLAlchemyError:
            raise PersistenceConflict() from None
        if result.rowcount != 1:
            raise PersistenceConflict()
        return RecoveryResult(operation_id, ExecutionOutcome.UNKNOWN_OUTCOME, 0, True)


def dispatch_claimed_operation(
    repository: ApprovalRepository,
    adapter: ExternalAdapter,
    operation_id: OperationId,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
) -> DispatchResult:
    """Compatibility entry point for the adapter-owned dispatch service."""

    from aaos.approval.adapters import dispatch_claimed_operation as _dispatch

    return _dispatch(
        repository,
        adapter,
        operation_id,
        capability_policy=capability_policy,
        definition=definition,
        now=now,
    )


def load_executable_action(
    repository: ApprovalRepository,
    operation_id: OperationId,
) -> ExecutableAction:
    """Load only the least-authority projection needed by an adapter."""

    from aaos.approval.adapters import load_executable_action as _load

    return _load(repository, operation_id)


__all__ = [
    "ClaimDecision",
    "ClaimResult",
    "ClaimSnapshot",
    "OutcomeRecordResult",
    "ReconciliationResult",
    "RecoveryResult",
    "Phase6ServiceError",
    "approve_approval",
    "claim_action",
    "adapter_outcome_to_execution_outcome",
    "dispatch_claimed_operation",
    "invalidate_for_trusted_drift",
    "load_executable_action",
    "prepare_action",
    "outcome_to_states",
    "record_adapter_outcome",
    "reconcile_operation",
    "recover_inflight_operation",
    "reject_approval",
    "request_approval",
    "revoke_approval",
    "validate_claim_snapshot",
]
