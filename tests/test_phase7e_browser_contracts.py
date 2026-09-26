from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aaos.tasks.contracts import BrowserTaskState, TaskKind
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalState,
    ApprovalSummary,
    CalendarCreateTaskRequest,
    SafeActionSummary,
    TaskDetail,
    TaskSummary,
)

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def action() -> SafeActionSummary:
    return SafeActionSummary(
        action_type="CALENDAR_CREATE_EVENT",
        integration_label="Google Calendar",
        summary="Office hours",
        start="2026-09-20T10:00:00.000000Z",
        end="2026-09-20T11:00:00.000000Z",
        timezone="Asia/Amman",
        description=None,
        location=None,
    )


def test_browser_task_models_have_exact_closed_shapes() -> None:
    task = TaskSummary(
        task_id="task-1",
        task_kind=TaskKind.CALENDAR_CREATE_EVENT,
        status=BrowserTaskState.AWAITING_APPROVAL,
        created_at=NOW,
        updated_at=NOW,
        action=action(),
    )
    assert set(task.model_dump()) == {
        "task_id",
        "task_kind",
        "status",
        "created_at",
        "updated_at",
        "action",
    }
    with pytest.raises(ValidationError):
        TaskSummary.model_validate({**task.model_dump(), "provider_payload": {}})


def test_task_request_rejects_authority_fields_and_coerced_values() -> None:
    request = CalendarCreateTaskRequest(
        summary="Office hours",
        start="2026-09-20T10:00:00.000000Z",
        end="2026-09-20T11:00:00.000000Z",
        timezone="Asia/Amman",
        description=None,
        location=None,
    )
    assert set(request.model_dump()) == {
        "summary",
        "start",
        "end",
        "timezone",
        "description",
        "location",
    }
    with pytest.raises(ValidationError):
        CalendarCreateTaskRequest.model_validate(
            {**request.model_dump(), "approver_identity": "attacker"}
        )
    with pytest.raises(ValidationError):
        CalendarCreateTaskRequest.model_validate(
            {
                "summary": 123,
                "start": request.start,
                "end": request.end,
                "timezone": request.timezone,
            }
        )


def test_detail_models_are_frozen_and_closed() -> None:
    approval = ApprovalSummary(
        approval_id="approval-1",
        task_id="task-1",
        action=action(),
        status=ApprovalState.PENDING,
        issued_at=NOW,
        approval_expires_at=NOW,
        operation_state=None,
        can_approve=True,
        can_reject=True,
    )
    detail = ApprovalDetail(
        **approval.model_dump(),
        decided_at=None,
        decision_reason=None,
    )
    task = TaskDetail(
        task_id="task-1",
        task_kind=TaskKind.CALENDAR_CREATE_EVENT,
        status=BrowserTaskState.AWAITING_APPROVAL,
        created_at=NOW,
        updated_at=NOW,
        action=action(),
        approval=approval,
        operation_state=None,
        operation_outcome=None,
        action_expires_at=NOW,
        failure_code=None,
    )
    assert detail.approval_id == "approval-1"
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        detail.approval_id = "other"
    with pytest.raises(ValidationError):
        TaskDetail.model_validate({**task.model_dump(), "raw_event": {}})
