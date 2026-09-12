"""Pure, source-aware Phase 6A lifecycle rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeVar

from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ApprovalId,
    ApprovalState,
    OperationId,
    OperationState,
    Phase6ErrorCode,
)


class TransitionSource(StrEnum):
    TRUSTED_SYSTEM = "TRUSTED_SYSTEM"
    HUMAN_APPROVER = "HUMAN_APPROVER"
    CLAIM_WORKER = "CLAIM_WORKER"
    DISPATCH_WORKER = "DISPATCH_WORKER"
    RECONCILIATION_WORKER = "RECONCILIATION_WORKER"


class Phase6StateError(ValueError):
    """A stable failure for a rejected closed state transition."""

    def __init__(self) -> None:
        self.code = Phase6ErrorCode.INVALID_STATE_TRANSITION
        super().__init__(self.code.value)


ACTION_TRANSITIONS: Mapping[ActionState, frozenset[ActionState]] = {
    ActionState.PREPARED: frozenset({ActionState.AWAITING_APPROVAL, ActionState.EXPIRED}),
    ActionState.AWAITING_APPROVAL: frozenset(
        {
            ActionState.APPROVED,
            ActionState.REJECTED,
            ActionState.EXPIRED,
            ActionState.REVOKED,
        }
    ),
    ActionState.APPROVED: frozenset(
        {
            ActionState.CLAIMED,
            ActionState.INVALIDATED,
            ActionState.EXPIRED,
            ActionState.REVOKED,
        }
    ),
    ActionState.CLAIMED: frozenset(
        {ActionState.EXECUTING, ActionState.INVALIDATED, ActionState.EXPIRED}
    ),
    ActionState.EXECUTING: frozenset(
        {
            ActionState.CONFIRMED_SUCCESS,
            ActionState.CONFIRMED_FAILURE,
            ActionState.UNKNOWN_OUTCOME,
            ActionState.INVALIDATED,
        }
    ),
    ActionState.UNKNOWN_OUTCOME: frozenset({ActionState.RECONCILIATION_PENDING}),
    ActionState.RECONCILIATION_PENDING: frozenset(
        {
            ActionState.CONFIRMED_SUCCESS,
            ActionState.CONFIRMED_FAILURE,
            ActionState.UNKNOWN_OUTCOME,
        }
    ),
}

APPROVAL_TRANSITIONS: Mapping[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.PENDING: frozenset(
        {
            ApprovalState.APPROVED,
            ApprovalState.REJECTED,
            ApprovalState.REVOKED,
            ApprovalState.EXPIRED,
        }
    ),
    ApprovalState.APPROVED: frozenset(
        {ApprovalState.CONSUMED, ApprovalState.REVOKED, ApprovalState.EXPIRED}
    ),
}

OPERATION_TRANSITIONS: Mapping[OperationState, frozenset[OperationState]] = {
    OperationState.CLAIMED: frozenset({OperationState.EXECUTING, OperationState.NOT_DISPATCHED}),
    OperationState.EXECUTING: frozenset(
        {
            OperationState.CONFIRMED_SUCCESS,
            OperationState.CONFIRMED_FAILURE,
            OperationState.UNKNOWN_OUTCOME,
            OperationState.NOT_DISPATCHED,
        }
    ),
    OperationState.UNKNOWN_OUTCOME: frozenset({OperationState.RECONCILIATION_PENDING}),
    OperationState.RECONCILIATION_PENDING: frozenset(
        {
            OperationState.CONFIRMED_SUCCESS,
            OperationState.CONFIRMED_FAILURE,
            OperationState.UNKNOWN_OUTCOME,
        }
    ),
}


_ACTION_SOURCES: Mapping[tuple[ActionState, ActionState], frozenset[TransitionSource]] = {
    (ActionState.PREPARED, ActionState.AWAITING_APPROVAL): frozenset(
        {TransitionSource.TRUSTED_SYSTEM}
    ),
    (ActionState.PREPARED, ActionState.EXPIRED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ActionState.AWAITING_APPROVAL, ActionState.APPROVED): frozenset(
        {TransitionSource.HUMAN_APPROVER}
    ),
    (ActionState.AWAITING_APPROVAL, ActionState.REJECTED): frozenset(
        {TransitionSource.HUMAN_APPROVER}
    ),
    (ActionState.AWAITING_APPROVAL, ActionState.REVOKED): frozenset(
        {TransitionSource.HUMAN_APPROVER, TransitionSource.TRUSTED_SYSTEM}
    ),
    (ActionState.AWAITING_APPROVAL, ActionState.EXPIRED): frozenset(
        {TransitionSource.TRUSTED_SYSTEM}
    ),
    (ActionState.APPROVED, ActionState.CLAIMED): frozenset({TransitionSource.CLAIM_WORKER}),
    (ActionState.APPROVED, ActionState.INVALIDATED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ActionState.APPROVED, ActionState.EXPIRED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ActionState.APPROVED, ActionState.REVOKED): frozenset(
        {TransitionSource.HUMAN_APPROVER, TransitionSource.TRUSTED_SYSTEM}
    ),
    (ActionState.CLAIMED, ActionState.EXECUTING): frozenset({TransitionSource.DISPATCH_WORKER}),
    (ActionState.CLAIMED, ActionState.INVALIDATED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ActionState.CLAIMED, ActionState.EXPIRED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ActionState.EXECUTING, ActionState.CONFIRMED_SUCCESS): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (ActionState.EXECUTING, ActionState.CONFIRMED_FAILURE): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (ActionState.EXECUTING, ActionState.UNKNOWN_OUTCOME): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (ActionState.EXECUTING, ActionState.INVALIDATED): frozenset({TransitionSource.DISPATCH_WORKER}),
    (ActionState.UNKNOWN_OUTCOME, ActionState.RECONCILIATION_PENDING): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (ActionState.RECONCILIATION_PENDING, ActionState.CONFIRMED_SUCCESS): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (ActionState.RECONCILIATION_PENDING, ActionState.CONFIRMED_FAILURE): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (ActionState.RECONCILIATION_PENDING, ActionState.UNKNOWN_OUTCOME): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
}

_APPROVAL_SOURCES: Mapping[tuple[ApprovalState, ApprovalState], frozenset[TransitionSource]] = {
    (ApprovalState.PENDING, ApprovalState.APPROVED): frozenset({TransitionSource.HUMAN_APPROVER}),
    (ApprovalState.PENDING, ApprovalState.REJECTED): frozenset({TransitionSource.HUMAN_APPROVER}),
    (ApprovalState.PENDING, ApprovalState.REVOKED): frozenset(
        {TransitionSource.HUMAN_APPROVER, TransitionSource.TRUSTED_SYSTEM}
    ),
    (ApprovalState.PENDING, ApprovalState.EXPIRED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
    (ApprovalState.APPROVED, ApprovalState.CONSUMED): frozenset({TransitionSource.CLAIM_WORKER}),
    (ApprovalState.APPROVED, ApprovalState.REVOKED): frozenset(
        {TransitionSource.HUMAN_APPROVER, TransitionSource.TRUSTED_SYSTEM}
    ),
    (ApprovalState.APPROVED, ApprovalState.EXPIRED): frozenset({TransitionSource.TRUSTED_SYSTEM}),
}

_OPERATION_SOURCES: Mapping[tuple[OperationState, OperationState], frozenset[TransitionSource]] = {
    (OperationState.CLAIMED, OperationState.EXECUTING): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.CLAIMED, OperationState.NOT_DISPATCHED): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.EXECUTING, OperationState.CONFIRMED_SUCCESS): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.EXECUTING, OperationState.CONFIRMED_FAILURE): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.EXECUTING, OperationState.UNKNOWN_OUTCOME): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.EXECUTING, OperationState.NOT_DISPATCHED): frozenset(
        {TransitionSource.DISPATCH_WORKER}
    ),
    (OperationState.UNKNOWN_OUTCOME, OperationState.RECONCILIATION_PENDING): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (OperationState.RECONCILIATION_PENDING, OperationState.CONFIRMED_SUCCESS): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (OperationState.RECONCILIATION_PENDING, OperationState.CONFIRMED_FAILURE): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
    (OperationState.RECONCILIATION_PENDING, OperationState.UNKNOWN_OUTCOME): frozenset(
        {TransitionSource.RECONCILIATION_WORKER}
    ),
}

State = TypeVar("State", ActionState, ApprovalState, OperationState)


def _validate_transition(
    current: State,
    target: State,
    source: object,
    state_type: type[State],
    transitions: Mapping[State, frozenset[State]],
    allowed_sources: Mapping[tuple[State, State], frozenset[TransitionSource]],
) -> None:
    if (
        not isinstance(current, state_type)
        or not isinstance(target, state_type)
        or not isinstance(source, TransitionSource)
        or target not in transitions.get(current, frozenset())
        or source not in allowed_sources.get((current, target), frozenset())
    ):
        raise Phase6StateError()


def validate_action_transition(
    current: ActionState, target: ActionState, source: TransitionSource
) -> None:
    _validate_transition(current, target, source, ActionState, ACTION_TRANSITIONS, _ACTION_SOURCES)


def validate_approval_transition(
    current: ApprovalState, target: ApprovalState, source: TransitionSource
) -> None:
    _validate_transition(
        current, target, source, ApprovalState, APPROVAL_TRANSITIONS, _APPROVAL_SOURCES
    )


def validate_operation_transition(
    current: OperationState, target: OperationState, source: TransitionSource
) -> None:
    _validate_transition(
        current, target, source, OperationState, OPERATION_TRANSITIONS, _OPERATION_SOURCES
    )


@dataclass(frozen=True, slots=True)
class FenceDecision:
    allowed: bool
    error_code: Phase6ErrorCode | None
    invalidates_action: bool


@dataclass(frozen=True, slots=True)
class DispatchFenceSnapshot:
    action_id: ActionId
    operation_id: OperationId
    lifecycle_state: ActionState
    operation_state: OperationState
    approval_state: ApprovalState
    consumed_approval_id: ApprovalId | None
    consumed_action_id: ActionId | None
    consumed_operation_id: OperationId | None
    capability_allowed: bool
    binding_matches: bool
    stored_precondition_intact: bool
    action_expires_at: datetime
    approval_expires_at: datetime
    dispatch_started_at: datetime | None
    operation_revision: int


def _rejected_fence(error_code: Phase6ErrorCode) -> FenceDecision:
    return FenceDecision(False, error_code, True)


def evaluate_dispatch_fence(snapshot: DispatchFenceSnapshot) -> FenceDecision:
    """Evaluate only facts already loaded in the dispatch transaction."""
    if not isinstance(snapshot, DispatchFenceSnapshot):
        raise TypeError("snapshot must be a DispatchFenceSnapshot")
    if (
        snapshot.lifecycle_state is not ActionState.CLAIMED
        or snapshot.operation_state is not OperationState.CLAIMED
    ):
        return _rejected_fence(Phase6ErrorCode.INVALID_STATE_TRANSITION)
    if snapshot.approval_state is not ApprovalState.CONSUMED:
        return _rejected_fence(Phase6ErrorCode.APPROVAL_NOT_FOUND)
    if (
        snapshot.consumed_approval_id is None
        or snapshot.consumed_action_id != snapshot.action_id
        or snapshot.consumed_operation_id != snapshot.operation_id
    ):
        return _rejected_fence(Phase6ErrorCode.CLAIM_CONFLICT)
    if not snapshot.capability_allowed:
        return _rejected_fence(Phase6ErrorCode.CAPABILITY_DENIED)
    if not snapshot.binding_matches:
        return _rejected_fence(Phase6ErrorCode.VERSION_MISMATCH)
    if not snapshot.stored_precondition_intact:
        return _rejected_fence(Phase6ErrorCode.PRECONDITION_STALE)
    if snapshot.dispatch_started_at is not None:
        return _rejected_fence(Phase6ErrorCode.DISPATCH_ALREADY_RECORDED)
    return FenceDecision(True, None, False)


__all__ = [name for name in globals() if not name.startswith("_")]
