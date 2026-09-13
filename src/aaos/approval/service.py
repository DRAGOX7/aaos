"""Trusted preparation and human approval transitions for Phase 6A."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, NoReturn, cast
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
    ApprovalDecision,
    ApprovalId,
    ApprovalRecord,
    ApprovalState,
    EventKind,
    HumanDecisionReason,
    OperationId,
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
)
from aaos.approval.events import (
    ActionEventDraft,
    ActionPreparedDetails,
    ApprovalDecisionDetails,
    ApprovalRequestedDetails,
    InvalidatedDetails,
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
)
from aaos.persistence.approval_models import (
    ActionLifecycleORM,
    ApprovalRecordORM,
    PreparedActionORM,
)

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


__all__ = [
    "Phase6ServiceError",
    "approve_approval",
    "invalidate_for_trusted_drift",
    "prepare_action",
    "reject_approval",
    "request_approval",
    "revoke_approval",
]
