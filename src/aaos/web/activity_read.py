"""Read-only Activity projection over the existing Phase 6/7E records."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import (
    EventKind,
    ExecutionOutcome,
    HumanDecisionReason,
    Phase6ErrorCode,
)
from aaos.approval.events import deserialize_event_payload
from aaos.persistence.approval_models import ActionEventORM
from aaos.persistence.task_models import TaskRecordORM
from aaos.tasks.contracts import TaskCreationState
from aaos.web.activity import (
    ActivityCategory,
    ActivityCursor,
    ActivityEvent,
    ActivityEventCode,
    ActivityHistoryResponse,
    ActivityResource,
    ActivitySourceRow,
    ActivityStatus,
    encode_activity_cursor,
)


class ActivitySourceRepository(Protocol):
    def list_source_rows(
        self,
        *,
        before: ActivityCursor | None,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[ActivitySourceRow, ...]: ...

    def current_high_watermark(self) -> ActivityCursor | None: ...


class ActivityReplayUnavailable(RuntimeError):
    """The requested cursor is no longer available for a safe replay."""


def _cursor(row: ActivitySourceRow) -> ActivityCursor:
    return ActivityCursor(
        row.occurred_at,
        1 if row.source_kind == "ACTION_EVENT" else 0,
        row.stable_source_id,
        row.source_sequence,
    )


def _safe_event_code(row: ActivitySourceRow) -> ActivityEventCode:
    if row.source_kind == "TASK_DERIVED":
        return (
            ActivityEventCode.PREPARATION_FAILED
            if row.task_failure_code is not None
            else ActivityEventCode.TASK_CREATED
        )
    if row.action_event_code is None:
        raise ValueError("action event has no public code")
    return row.action_event_code


def project_source_row(row: ActivitySourceRow) -> ActivityEvent:
    """Convert one trusted internal row into the closed browser projection."""

    code = _safe_event_code(row)
    if row.source_kind == "TASK_DERIVED":
        return ActivityEvent(
            schema_version="phase7-activity-v1",
            activity_id=f"task:{row.stable_source_id}:{code.value}",
            occurred_at=row.occurred_at,
            category=ActivityCategory.TASK,
            event_code=code,
            status=None,
            resource_type=ActivityResource.TASK,
            resource_id=row.task_id,
            title="Task created"
            if code is ActivityEventCode.TASK_CREATED
            else "Task preparation failed",
            detail=None,
            context=None,
        )

    category = (
        ActivityCategory.APPROVAL
        if code
        in {
            ActivityEventCode.APPROVAL_REQUESTED,
            ActivityEventCode.APPROVED,
            ActivityEventCode.REJECTED,
            ActivityEventCode.REVOKED,
            ActivityEventCode.EXPIRED,
        }
        else ActivityCategory.INTEGRATION
        if code
        in {
            ActivityEventCode.CLAIMED,
            ActivityEventCode.EXECUTING,
            ActivityEventCode.CONFIRMED_SUCCESS,
            ActivityEventCode.CONFIRMED_FAILURE,
            ActivityEventCode.UNKNOWN_OUTCOME,
            ActivityEventCode.RECONCILED,
        }
        else ActivityCategory.TASK
    )
    title = {
        ActivityEventCode.ACTION_PREPARED: "Action prepared",
        ActivityEventCode.APPROVAL_REQUESTED: "Approval requested",
        ActivityEventCode.APPROVED: "Approval granted",
        ActivityEventCode.REJECTED: "Approval rejected",
        ActivityEventCode.REVOKED: "Approval revoked",
        ActivityEventCode.EXPIRED: "Approval expired",
        ActivityEventCode.CLAIMED: "Action claimed",
        ActivityEventCode.EXECUTING: "Action executing",
        ActivityEventCode.CONFIRMED_SUCCESS: "Action succeeded",
        ActivityEventCode.CONFIRMED_FAILURE: "Action failed",
        ActivityEventCode.UNKNOWN_OUTCOME: "Action outcome unknown",
        ActivityEventCode.NOT_DISPATCHED
        if hasattr(ActivityEventCode, "NOT_DISPATCHED")
        else ActivityEventCode.INVALIDATED: "Action invalidated",
        ActivityEventCode.INVALIDATED: "Action invalidated",
        ActivityEventCode.RECONCILED: "Action reconciled",
    }.get(code, "Activity update")
    resource_type = ActivityResource.APPROVAL if row.approval_id else ActivityResource.TASK
    return ActivityEvent(
        schema_version="phase7-activity-v1",
        activity_id=f"event:{row.stable_source_id}:{row.source_sequence}",
        occurred_at=row.occurred_at,
        category=category,
        event_code=code,
        status=row.approval_status or row.operation_status,
        resource_type=resource_type,
        resource_id=row.approval_id or row.task_id,
        title=title,
        detail=None,
        context=None,
    )


class ActivityReadRepository:
    """Read-only SQLAlchemy repository for the composite Activity projection."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        if not isinstance(sessions, sessionmaker):
            raise TypeError("sessions must be a SQLAlchemy sessionmaker")
        self._sessions = sessions

    def _rows(self, session: Session) -> list[ActivitySourceRow]:
        tasks = session.scalars(select(TaskRecordORM)).all()
        task_by_action = {row.action_id: row.task_id for row in tasks if row.action_id is not None}
        rows: list[ActivitySourceRow] = []
        for task in tasks:
            rows.append(
                ActivitySourceRow(
                    source_kind="TASK_DERIVED",
                    occurred_at=cast(datetime, task.created_at),
                    stable_source_id=task.task_id,
                    source_sequence=0,
                    task_id=task.task_id,
                    approval_id=None,
                    action_event_code=None,
                    task_creation_state=task.creation_state,
                    task_failure_code=task.failure_code,
                    approval_status=None,
                    operation_status=None,
                    typed_detail=None,
                )
            )
            if task.creation_state == TaskCreationState.FAILED.value:
                rows.append(
                    ActivitySourceRow(
                        source_kind="TASK_DERIVED",
                        occurred_at=cast(datetime, task.updated_at),
                        stable_source_id=task.task_id,
                        source_sequence=1,
                        task_id=task.task_id,
                        approval_id=None,
                        action_event_code=None,
                        task_creation_state=task.creation_state,
                        task_failure_code=task.failure_code,
                        approval_status=None,
                        operation_status=None,
                        typed_detail=None,
                    )
                )
        events = session.scalars(select(ActionEventORM)).all()
        for event in events:
            task_id = task_by_action.get(event.action_id)
            if task_id is None:
                continue
            try:
                event_kind = EventKind(event.event_kind)
                _operation_id, _source, _actor, details = deserialize_event_payload(
                    event.detail_payload, event_kind=event_kind
                )
            except (TypeError, ValueError):
                continue
            if event_kind is EventKind.LATE_RESULT_IGNORED:
                continue
            code, status, approval_id = self._event_projection(event_kind, details)
            rows.append(
                ActivitySourceRow(
                    source_kind="ACTION_EVENT",
                    occurred_at=event.occurred_at,
                    stable_source_id=event.event_id,
                    source_sequence=event.sequence,
                    task_id=task_id,
                    approval_id=approval_id,
                    action_event_code=code,
                    task_creation_state=None,
                    task_failure_code=None,
                    approval_status=status
                    if code
                    in {
                        ActivityEventCode.APPROVAL_REQUESTED,
                        ActivityEventCode.APPROVED,
                        ActivityEventCode.REJECTED,
                        ActivityEventCode.REVOKED,
                        ActivityEventCode.EXPIRED,
                    }
                    else None,
                    operation_status=status
                    if code
                    in {
                        ActivityEventCode.CLAIMED,
                        ActivityEventCode.EXECUTING,
                        ActivityEventCode.CONFIRMED_SUCCESS,
                        ActivityEventCode.CONFIRMED_FAILURE,
                        ActivityEventCode.UNKNOWN_OUTCOME,
                        ActivityEventCode.RECONCILED,
                    }
                    else None,
                    typed_detail=None,
                )
            )
        return rows

    @staticmethod
    def _event_projection(
        event_kind: EventKind, details: object
    ) -> tuple[ActivityEventCode, ActivityStatus | None, str | None]:
        if event_kind is EventKind.ACTION_PREPARED:
            return ActivityEventCode.ACTION_PREPARED, ActivityStatus.PENDING, None
        if event_kind is EventKind.APPROVAL_REQUESTED:
            approval_id = getattr(details, "approval_id", None)
            return (
                ActivityEventCode.APPROVAL_REQUESTED,
                ActivityStatus.PENDING,
                str(approval_id) if approval_id else None,
            )
        if event_kind is EventKind.APPROVAL_DECIDED:
            approval_id = getattr(details, "approval_id", None)
            reason = getattr(details, "reason", None)
            mapping = {
                HumanDecisionReason.USER_APPROVED: (
                    ActivityEventCode.APPROVED,
                    ActivityStatus.APPROVED,
                ),
                HumanDecisionReason.USER_REJECTED: (
                    ActivityEventCode.REJECTED,
                    ActivityStatus.REJECTED,
                ),
                HumanDecisionReason.USER_REVOKED: (
                    ActivityEventCode.REVOKED,
                    ActivityStatus.REVOKED,
                ),
            }
            if not isinstance(reason, HumanDecisionReason):
                return (
                    ActivityEventCode.INVALIDATED,
                    ActivityStatus.INVALIDATED,
                    str(approval_id) if approval_id else None,
                )
            code, status = mapping[reason]
            return code, status, str(approval_id) if approval_id else None
        if event_kind is EventKind.APPROVAL_CONSUMED:
            approval_id = getattr(details, "approval_id", None)
            return (
                ActivityEventCode.CLAIMED,
                ActivityStatus.CLAIMED,
                str(approval_id) if approval_id else None,
            )
        if event_kind is EventKind.DISPATCH_STARTED:
            return ActivityEventCode.EXECUTING, ActivityStatus.EXECUTING, None
        if event_kind is EventKind.OUTCOME_RECORDED:
            outcome = getattr(details, "outcome", None)
            reason = getattr(details, "reason", None)
            if reason is Phase6ErrorCode.PRECONDITION_STALE:
                return ActivityEventCode.INVALIDATED, ActivityStatus.NOT_DISPATCHED, None
            if outcome is ExecutionOutcome.CONFIRMED_SUCCESS:
                return ActivityEventCode.CONFIRMED_SUCCESS, ActivityStatus.CONFIRMED_SUCCESS, None
            if outcome is ExecutionOutcome.CONFIRMED_FAILURE:
                return ActivityEventCode.CONFIRMED_FAILURE, ActivityStatus.CONFIRMED_FAILURE, None
            return ActivityEventCode.UNKNOWN_OUTCOME, ActivityStatus.UNKNOWN_OUTCOME, None
        if event_kind is EventKind.RECONCILIATION_RECORDED:
            return (
                ActivityEventCode.RECONCILED,
                ActivityStatus(getattr(details, "outcome").value),
                None,
            )
        reason = getattr(details, "reason", None)
        if getattr(reason, "value", None) == "ACTION_EXPIRED":
            return ActivityEventCode.EXPIRED, ActivityStatus.EXPIRED, None
        return ActivityEventCode.INVALIDATED, ActivityStatus.INVALIDATED, None

    def list_source_rows(
        self,
        *,
        before: ActivityCursor | None,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[ActivitySourceRow, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._sessions() as session:
            rows = sorted(self._rows(session), key=_cursor)
        if before is not None:
            rows = [row for row in rows if _cursor(row) < before]
            rows = list(reversed(rows[-limit:]))
        elif after is not None:
            rows = [row for row in rows if _cursor(row) > after][:limit]
        else:
            rows = list(reversed(rows[-limit:]))
        return tuple(rows)

    def current_high_watermark(self) -> ActivityCursor | None:
        with self._sessions() as session:
            rows = self._rows(session)
        return max((_cursor(row) for row in rows), default=None)


class ActivityReadService:
    """Read-only Activity history and tail projection."""

    def __init__(self, repository: ActivitySourceRepository) -> None:
        self._repository = repository

    def list_history(
        self,
        *,
        limit: int,
        before: ActivityCursor | None = None,
    ) -> ActivityHistoryResponse:
        rows = self._repository.list_source_rows(before=before, after=None, limit=limit)
        events = tuple(project_source_row(row) for row in rows)
        next_cursor = encode_activity_cursor(_cursor(rows[-1])) if rows else None
        return ActivityHistoryResponse(
            schema_version="phase7-activity-v1",
            events=events,
            next_cursor=next_cursor,
        )

    def tail(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[ActivityEvent, ...]:
        return tuple(event for event, _cursor in self.tail_with_cursors(after=after, limit=limit))

    def tail_with_cursors(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]:
        rows = self._repository.list_source_rows(before=None, after=after, limit=limit)
        return tuple((project_source_row(row), _cursor(row)) for row in rows)

    def current_high_watermark(self) -> ActivityCursor | None:
        return self._repository.current_high_watermark()


__all__ = [
    "ActivityReadRepository",
    "ActivityReadService",
    "ActivityReplayUnavailable",
    "project_source_row",
]
