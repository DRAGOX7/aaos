from datetime import datetime, timezone

import pytest

from aaos.approval.contracts import ActionState, ApprovalState, OperationState
from aaos.tasks.contracts import (
    BrowserTaskState,
    TaskCreationState,
    TaskFailureCode,
    TaskKind,
    project_task_state,
)


def test_task_enums_are_closed() -> None:
    assert {member.value for member in TaskKind} == {"CALENDAR_CREATE_EVENT"}
    assert {member.value for member in TaskCreationState} == {
        "CREATED",
        "ACTION_BOUND",
        "FAILED",
    }
    assert {member.value for member in TaskFailureCode} == {
        "INVALID_REQUEST",
        "CAPABILITY_DENIED",
        "PREPARATION_FAILED",
    }
    assert "UNKNOWN_OUTCOME" in {member.value for member in BrowserTaskState}


@pytest.mark.parametrize(
    ("action", "approval", "operation", "expected"),
    [
        (None, None, None, BrowserTaskState.PREPARING),
        (ActionState.PREPARED, None, None, BrowserTaskState.PREPARING),
        (
            ActionState.AWAITING_APPROVAL,
            ApprovalState.PENDING,
            None,
            BrowserTaskState.AWAITING_APPROVAL,
        ),
        (ActionState.APPROVED, ApprovalState.APPROVED, None, BrowserTaskState.APPROVED),
        (
            ActionState.CLAIMED,
            ApprovalState.CONSUMED,
            OperationState.CLAIMED,
            BrowserTaskState.CLAIMED,
        ),
        (
            ActionState.EXECUTING,
            ApprovalState.CONSUMED,
            OperationState.EXECUTING,
            BrowserTaskState.EXECUTING,
        ),
        (
            ActionState.UNKNOWN_OUTCOME,
            ApprovalState.CONSUMED,
            OperationState.UNKNOWN_OUTCOME,
            BrowserTaskState.UNKNOWN_OUTCOME,
        ),
        (
            ActionState.CONFIRMED_SUCCESS,
            ApprovalState.CONSUMED,
            OperationState.CONFIRMED_SUCCESS,
            BrowserTaskState.CONFIRMED_SUCCESS,
        ),
        (
            ActionState.CONFIRMED_FAILURE,
            ApprovalState.CONSUMED,
            OperationState.CONFIRMED_FAILURE,
            BrowserTaskState.CONFIRMED_FAILURE,
        ),
        (ActionState.REJECTED, ApprovalState.REJECTED, None, BrowserTaskState.REJECTED),
        (ActionState.INVALIDATED, ApprovalState.REVOKED, None, BrowserTaskState.INVALIDATED),
        (ActionState.EXPIRED, ApprovalState.EXPIRED, None, BrowserTaskState.EXPIRED),
        (ActionState.REVOKED, ApprovalState.REVOKED, None, BrowserTaskState.REVOKED),
    ],
)
def test_project_task_state_is_deterministic(
    action: ActionState | None,
    approval: ApprovalState | None,
    operation: OperationState | None,
    expected: BrowserTaskState,
) -> None:
    assert (
        project_task_state(TaskCreationState.ACTION_BOUND, action, approval, operation) is expected
    )


def test_failed_creation_projects_to_preparation_failed() -> None:
    assert (
        project_task_state(TaskCreationState.FAILED, None, None, None)
        is BrowserTaskState.PREPARATION_FAILED
    )


def test_public_task_record_is_immutable_and_bounded() -> None:
    from aaos.approval.contracts import ActionId, ActorIdentity
    from aaos.tasks.contracts import TaskRecord

    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    record = TaskRecord(
        task_id="task-1",
        task_kind=TaskKind.CALENDAR_CREATE_EVENT,
        actor_identity=ActorIdentity("local-user"),
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        creation_state=TaskCreationState.CREATED,
        failure_code=None,
        action_id=ActionId("action-1"),
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(AttributeError):
        record.task_id = "other"  # type: ignore[misc]
