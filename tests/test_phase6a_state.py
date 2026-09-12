"""Closed lifecycle and dispatch-fence rules for Phase 6A."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import TypeVar, cast

import pytest

from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ApprovalId,
    ApprovalState,
    OperationId,
    OperationState,
    Phase6ErrorCode,
)
from aaos.approval.state import (
    ACTION_TRANSITIONS,
    APPROVAL_TRANSITIONS,
    OPERATION_TRANSITIONS,
    DispatchFenceSnapshot,
    Phase6StateError,
    TransitionSource,
    evaluate_dispatch_fence,
    validate_action_transition,
    validate_approval_transition,
    validate_operation_transition,
)

LEGAL_ACTION_TRANSITIONS: tuple[tuple[ActionState, ActionState, TransitionSource], ...] = (
    (ActionState.PREPARED, ActionState.AWAITING_APPROVAL, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.PREPARED, ActionState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.AWAITING_APPROVAL, ActionState.APPROVED, TransitionSource.HUMAN_APPROVER),
    (ActionState.AWAITING_APPROVAL, ActionState.REJECTED, TransitionSource.HUMAN_APPROVER),
    (ActionState.AWAITING_APPROVAL, ActionState.REVOKED, TransitionSource.HUMAN_APPROVER),
    (ActionState.AWAITING_APPROVAL, ActionState.REVOKED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.AWAITING_APPROVAL, ActionState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.APPROVED, ActionState.CLAIMED, TransitionSource.CLAIM_WORKER),
    (ActionState.APPROVED, ActionState.INVALIDATED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.APPROVED, ActionState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.APPROVED, ActionState.REVOKED, TransitionSource.HUMAN_APPROVER),
    (ActionState.APPROVED, ActionState.REVOKED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.CLAIMED, ActionState.EXECUTING, TransitionSource.DISPATCH_WORKER),
    (ActionState.CLAIMED, ActionState.INVALIDATED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.CLAIMED, ActionState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
    (ActionState.EXECUTING, ActionState.CONFIRMED_SUCCESS, TransitionSource.DISPATCH_WORKER),
    (ActionState.EXECUTING, ActionState.CONFIRMED_FAILURE, TransitionSource.DISPATCH_WORKER),
    (ActionState.EXECUTING, ActionState.UNKNOWN_OUTCOME, TransitionSource.DISPATCH_WORKER),
    (ActionState.EXECUTING, ActionState.INVALIDATED, TransitionSource.DISPATCH_WORKER),
    (
        ActionState.UNKNOWN_OUTCOME,
        ActionState.RECONCILIATION_PENDING,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        ActionState.RECONCILIATION_PENDING,
        ActionState.CONFIRMED_SUCCESS,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        ActionState.RECONCILIATION_PENDING,
        ActionState.CONFIRMED_FAILURE,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        ActionState.RECONCILIATION_PENDING,
        ActionState.UNKNOWN_OUTCOME,
        TransitionSource.RECONCILIATION_WORKER,
    ),
)

LEGAL_APPROVAL_TRANSITIONS: tuple[tuple[ApprovalState, ApprovalState, TransitionSource], ...] = (
    (ApprovalState.PENDING, ApprovalState.APPROVED, TransitionSource.HUMAN_APPROVER),
    (ApprovalState.PENDING, ApprovalState.REJECTED, TransitionSource.HUMAN_APPROVER),
    (ApprovalState.PENDING, ApprovalState.REVOKED, TransitionSource.HUMAN_APPROVER),
    (ApprovalState.PENDING, ApprovalState.REVOKED, TransitionSource.TRUSTED_SYSTEM),
    (ApprovalState.PENDING, ApprovalState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
    (ApprovalState.APPROVED, ApprovalState.CONSUMED, TransitionSource.CLAIM_WORKER),
    (ApprovalState.APPROVED, ApprovalState.REVOKED, TransitionSource.HUMAN_APPROVER),
    (ApprovalState.APPROVED, ApprovalState.REVOKED, TransitionSource.TRUSTED_SYSTEM),
    (ApprovalState.APPROVED, ApprovalState.EXPIRED, TransitionSource.TRUSTED_SYSTEM),
)

LEGAL_OPERATION_TRANSITIONS: tuple[tuple[OperationState, OperationState, TransitionSource], ...] = (
    (OperationState.CLAIMED, OperationState.EXECUTING, TransitionSource.DISPATCH_WORKER),
    (OperationState.CLAIMED, OperationState.NOT_DISPATCHED, TransitionSource.DISPATCH_WORKER),
    (OperationState.EXECUTING, OperationState.CONFIRMED_SUCCESS, TransitionSource.DISPATCH_WORKER),
    (OperationState.EXECUTING, OperationState.CONFIRMED_FAILURE, TransitionSource.DISPATCH_WORKER),
    (OperationState.EXECUTING, OperationState.UNKNOWN_OUTCOME, TransitionSource.DISPATCH_WORKER),
    (OperationState.EXECUTING, OperationState.NOT_DISPATCHED, TransitionSource.DISPATCH_WORKER),
    (
        OperationState.UNKNOWN_OUTCOME,
        OperationState.RECONCILIATION_PENDING,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        OperationState.RECONCILIATION_PENDING,
        OperationState.CONFIRMED_SUCCESS,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        OperationState.RECONCILIATION_PENDING,
        OperationState.CONFIRMED_FAILURE,
        TransitionSource.RECONCILIATION_WORKER,
    ),
    (
        OperationState.RECONCILIATION_PENDING,
        OperationState.UNKNOWN_OUTCOME,
        TransitionSource.RECONCILIATION_WORKER,
    ),
)


State = TypeVar("State", ActionState, ApprovalState, OperationState)


def _illegal_pairs(
    states: type[State],
    transitions: Mapping[State, frozenset[State]],
) -> tuple[tuple[State, State], ...]:
    return tuple(
        (current, target)
        for current in states
        for target in states
        if target not in transitions.get(current, frozenset())
    )


@pytest.mark.parametrize(("current", "target", "source"), LEGAL_ACTION_TRANSITIONS)
def test_legal_action_transition_is_accepted(
    current: ActionState, target: ActionState, source: TransitionSource
) -> None:
    validate_action_transition(current, target, source)


@pytest.mark.parametrize(("current", "target"), _illegal_pairs(ActionState, ACTION_TRANSITIONS))
def test_unlisted_action_transition_is_rejected(current: ActionState, target: ActionState) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_action_transition(current, target, TransitionSource.TRUSTED_SYSTEM)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(
    ("current", "target", "source"),
    (
        (ActionState.AWAITING_APPROVAL, ActionState.REJECTED, TransitionSource.TRUSTED_SYSTEM),
        (ActionState.AWAITING_APPROVAL, ActionState.APPROVED, TransitionSource.TRUSTED_SYSTEM),
        (ActionState.APPROVED, ActionState.CLAIMED, TransitionSource.HUMAN_APPROVER),
        (ActionState.APPROVED, ActionState.INVALIDATED, TransitionSource.HUMAN_APPROVER),
    ),
)
def test_action_transition_rejects_wrong_source(
    current: ActionState, target: ActionState, source: TransitionSource
) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_action_transition(current, target, source)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(("current", "target", "source"), LEGAL_APPROVAL_TRANSITIONS)
def test_legal_approval_transition_is_accepted(
    current: ApprovalState, target: ApprovalState, source: TransitionSource
) -> None:
    validate_approval_transition(current, target, source)


@pytest.mark.parametrize(("current", "target"), _illegal_pairs(ApprovalState, APPROVAL_TRANSITIONS))
def test_unlisted_approval_transition_is_rejected(
    current: ApprovalState, target: ApprovalState
) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_approval_transition(current, target, TransitionSource.TRUSTED_SYSTEM)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(
    ("current", "target", "source"),
    (
        (ApprovalState.PENDING, ApprovalState.APPROVED, TransitionSource.TRUSTED_SYSTEM),
        (ApprovalState.APPROVED, ApprovalState.CONSUMED, TransitionSource.HUMAN_APPROVER),
        (ApprovalState.APPROVED, ApprovalState.EXPIRED, TransitionSource.HUMAN_APPROVER),
    ),
)
def test_approval_transition_rejects_wrong_source(
    current: ApprovalState, target: ApprovalState, source: TransitionSource
) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_approval_transition(current, target, source)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(("current", "target", "source"), LEGAL_OPERATION_TRANSITIONS)
def test_legal_operation_transition_is_accepted(
    current: OperationState, target: OperationState, source: TransitionSource
) -> None:
    validate_operation_transition(current, target, source)


@pytest.mark.parametrize(
    ("current", "target"), _illegal_pairs(OperationState, OPERATION_TRANSITIONS)
)
def test_unlisted_operation_transition_is_rejected(
    current: OperationState, target: OperationState
) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_operation_transition(current, target, TransitionSource.TRUSTED_SYSTEM)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize(
    ("current", "target", "source"),
    (
        (OperationState.CLAIMED, OperationState.EXECUTING, TransitionSource.CLAIM_WORKER),
        (
            OperationState.EXECUTING,
            OperationState.UNKNOWN_OUTCOME,
            TransitionSource.RECONCILIATION_WORKER,
        ),
        (
            OperationState.UNKNOWN_OUTCOME,
            OperationState.RECONCILIATION_PENDING,
            TransitionSource.DISPATCH_WORKER,
        ),
    ),
)
def test_operation_transition_rejects_wrong_source(
    current: OperationState, target: OperationState, source: TransitionSource
) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_operation_transition(current, target, source)
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION


def make_snapshot(**changes: object) -> DispatchFenceSnapshot:
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    fields: dict[str, object] = {
        "action_id": ActionId("action-001"),
        "operation_id": OperationId("operation-001"),
        "lifecycle_state": ActionState.CLAIMED,
        "operation_state": OperationState.CLAIMED,
        "approval_state": ApprovalState.CONSUMED,
        "consumed_approval_id": ApprovalId("approval-001"),
        "consumed_action_id": ActionId("action-001"),
        "consumed_operation_id": OperationId("operation-001"),
        "capability_allowed": True,
        "binding_matches": True,
        "stored_precondition_intact": True,
        "action_expires_at": now + timedelta(minutes=10),
        "approval_expires_at": now + timedelta(minutes=5),
        "evaluated_at": now,
        "dispatch_started_at": None,
        "operation_revision": 3,
    }
    fields.update(changes)
    return DispatchFenceSnapshot(**fields)  # type: ignore[arg-type]


def test_dispatch_fence_accepts_complete_claimed_snapshot() -> None:
    assert evaluate_dispatch_fence(make_snapshot()).allowed


@pytest.mark.parametrize(
    ("expiry_field", "expected"),
    (
        ("action_expires_at", Phase6ErrorCode.APPROVAL_EXPIRED),
        ("approval_expires_at", Phase6ErrorCode.APPROVAL_EXPIRED),
    ),
)
def test_dispatch_fence_fails_closed_when_a_bound_deadline_has_expired(
    expiry_field: str, expected: Phase6ErrorCode
) -> None:
    evaluated_at = datetime(2026, 9, 12, tzinfo=timezone.utc)
    decision = evaluate_dispatch_fence(
        make_snapshot(**{expiry_field: evaluated_at, "evaluated_at": evaluated_at})
    )
    assert not decision.allowed
    assert decision.error_code is expected
    assert not decision.invalidates_action


@pytest.mark.parametrize(
    ("changes", "expected"),
    (
        ({"approval_state": ApprovalState.APPROVED}, None),
        ({"consumed_approval_id": None}, None),
        ({"consumed_action_id": ActionId("other-action")}, None),
        ({"consumed_operation_id": OperationId("other-operation")}, None),
        ({"capability_allowed": False}, Phase6ErrorCode.CAPABILITY_DENIED),
        ({"binding_matches": False}, Phase6ErrorCode.VERSION_MISMATCH),
        ({"stored_precondition_intact": False}, Phase6ErrorCode.PRECONDITION_STALE),
        (
            {"dispatch_started_at": datetime(2026, 9, 12, tzinfo=timezone.utc)},
            Phase6ErrorCode.DISPATCH_ALREADY_RECORDED,
        ),
        ({"lifecycle_state": ActionState.EXECUTING}, Phase6ErrorCode.INVALID_STATE_TRANSITION),
        ({"operation_state": OperationState.EXECUTING}, Phase6ErrorCode.INVALID_STATE_TRANSITION),
    ),
)
def test_dispatch_fence_rejects_each_failed_stored_predicate(
    changes: dict[str, object], expected: Phase6ErrorCode | None
) -> None:
    decision = evaluate_dispatch_fence(make_snapshot(**changes))
    assert not decision.allowed
    assert decision.invalidates_action
    if expected is not None:
        assert decision.error_code is expected


def test_dispatch_fence_is_a_pure_stored_snapshot_predicate() -> None:
    snapshot = make_snapshot()
    assert evaluate_dispatch_fence(snapshot) == evaluate_dispatch_fence(snapshot)


@pytest.mark.parametrize("value", ("CLAIMED", None, 1))
def test_transition_validator_rejects_unknown_state_values(value: object) -> None:
    with pytest.raises(Phase6StateError) as error:
        validate_action_transition(
            cast(ActionState, value), ActionState.EXECUTING, TransitionSource.DISPATCH_WORKER
        )
    assert error.value.code is Phase6ErrorCode.INVALID_STATE_TRANSITION
