from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from aaos.web.activity import (
    ActivityCategory,
    ActivityContext,
    ActivityEvent,
    ActivityEventCode,
    ActivityHistoryResponse,
    ActivityResource,
    ActivityStatus,
    ApprovalActivityContext,
    OperationActivityContext,
    TaskActivityContext,
    decode_activity_cursor,
    encode_activity_cursor,
)


def _event() -> ActivityEvent:
    return ActivityEvent(
        schema_version="phase7-activity-v1",
        activity_id="evt-1",
        occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        category=ActivityCategory.TASK,
        event_code=ActivityEventCode.TASK_CREATED,
        status=ActivityStatus.PENDING,
        resource_type=ActivityResource.TASK,
        resource_id="task-1",
        title="Task created",
        detail=None,
        context=TaskActivityContext(kind="TASK", task_id="task-1"),
    )


def test_activity_event_has_closed_browser_safe_shape() -> None:
    event = _event()
    assert set(event.model_dump()) == {
        "schema_version",
        "activity_id",
        "occurred_at",
        "category",
        "event_code",
        "status",
        "resource_type",
        "resource_id",
        "title",
        "detail",
        "context",
    }
    with pytest.raises(ValidationError):
        ActivityEvent(**{**event.model_dump(), "operation_id": "secret"})


def test_activity_context_is_a_closed_union() -> None:
    adapter = TypeAdapter(ActivityContext)
    assert isinstance(
        adapter.validate_python({"kind": "TASK", "task_id": "task-1"}), TaskActivityContext
    )
    assert isinstance(
        adapter.validate_python({"kind": "APPROVAL", "approval_id": "a", "task_id": "task-1"}),
        ApprovalActivityContext,
    )
    assert isinstance(
        adapter.validate_python({"kind": "OPERATION", "status": ActivityStatus.UNKNOWN_OUTCOME}),
        OperationActivityContext,
    )
    with pytest.raises(ValidationError):
        ActivityEvent(**{**_event().model_dump(), "context": {"kind": "RAW", "value": "secret"}})


def test_activity_history_uses_immutable_tuple_projection() -> None:
    response = ActivityHistoryResponse(
        schema_version="phase7-activity-v1",
        events=(_event(),),
        next_cursor=None,
    )
    assert isinstance(response.events, tuple)
    with pytest.raises(ValidationError):
        response.events = ()


def test_cursor_round_trips_without_internal_payload() -> None:
    value = encode_activity_cursor(
        (
            datetime(2026, 9, 18, tzinfo=timezone.utc),
            1,
            "event-1",
            4,
        )
    )
    decoded = decode_activity_cursor(value)
    assert decoded[2] == "event-1"
    assert "operation_id" not in value


@pytest.mark.parametrize("code", list(ActivityEventCode))
def test_event_code_enum_is_closed(code: ActivityEventCode) -> None:
    assert ActivityEventCode(code.value) is code


def test_unknown_event_code_is_rejected() -> None:
    with pytest.raises(ValueError):
        ActivityEventCode("RAW_PROVIDER_EVENT")
