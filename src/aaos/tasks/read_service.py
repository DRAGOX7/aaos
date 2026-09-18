"""Browser-safe read projections over the Phase 7E and Phase 6 ledgers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import (
    ActionState,
    ApprovalRecord,
    ApprovalState,
    OperationState,
    PreparedAction,
)
from aaos.approval.service import _approval_from_orm, _prepared_from_orm
from aaos.persistence.approval_models import (
    ActionLifecycleORM,
    ApprovalRecordORM,
    OperationRecordORM,
    PreparedActionORM,
)
from aaos.persistence.task_models import TaskRecordORM
from aaos.tasks.contracts import TaskRecord, project_task_state
from aaos.tasks.repository import TaskRepository
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalSummary,
    SafeActionSummary,
    TaskDetail,
    TaskSummary,
)
from aaos.web.errors import Phase7ApiError


def _safe_action(prepared: PreparedAction) -> SafeActionSummary:
    from aaos.tasks.service import _safe_action as project_action

    return project_action(prepared)


class TaskReadService:
    """Read-only service that never mutates task or approval state."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        if not isinstance(sessions, sessionmaker):
            raise TypeError("sessions must be a SQLAlchemy sessionmaker")
        self._sessions = sessions
        self._tasks = TaskRepository(sessions)

    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]:
        records = self._tasks.list(limit=limit)
        return tuple(self._summary(self._detail(record)) for record in records)

    def get_task(self, task_id: str) -> TaskDetail:
        record = self._tasks.get(task_id)
        if record is None:
            raise Phase7ApiError(404, "TASK_NOT_FOUND", "The requested task is not available.")
        return self._detail(record)

    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(
                    ApprovalRecordORM,
                    PreparedActionORM,
                    ActionLifecycleORM,
                    OperationRecordORM,
                    TaskRecordORM,
                )
                .join(
                    PreparedActionORM,
                    PreparedActionORM.action_id == ApprovalRecordORM.action_id,
                )
                .join(
                    ActionLifecycleORM,
                    ActionLifecycleORM.action_id == ApprovalRecordORM.action_id,
                )
                .join(
                    TaskRecordORM,
                    TaskRecordORM.action_id == ApprovalRecordORM.action_id,
                )
                .outerjoin(
                    OperationRecordORM,
                    OperationRecordORM.action_id == ApprovalRecordORM.action_id,
                )
                .order_by(ApprovalRecordORM.issued_at.asc(), ApprovalRecordORM.approval_id.asc())
                .limit(limit)
            ).all()
            return tuple(self._approval_summary(row) for row in rows)

    def get_approval(self, approval_id: str) -> ApprovalDetail:
        with self._sessions() as session:
            row = session.execute(
                select(
                    ApprovalRecordORM,
                    PreparedActionORM,
                    ActionLifecycleORM,
                    OperationRecordORM,
                    TaskRecordORM,
                )
                .join(
                    PreparedActionORM,
                    PreparedActionORM.action_id == ApprovalRecordORM.action_id,
                )
                .join(
                    ActionLifecycleORM,
                    ActionLifecycleORM.action_id == ApprovalRecordORM.action_id,
                )
                .join(
                    TaskRecordORM,
                    TaskRecordORM.action_id == ApprovalRecordORM.action_id,
                )
                .outerjoin(
                    OperationRecordORM,
                    OperationRecordORM.action_id == ApprovalRecordORM.action_id,
                )
                .where(ApprovalRecordORM.approval_id == approval_id)
            ).first()
            if row is None:
                raise Phase7ApiError(
                    404,
                    "APPROVAL_NOT_FOUND",
                    "The requested approval is not available.",
                )
            approval = self._approval_summary(row)
            approval_row, _prepared_row, _lifecycle, _operation, _task = row
            return ApprovalDetail(
                **approval.model_dump(),
                decided_at=approval_row.decided_at,
                decision_reason=approval_row.reason_code,
            )

    def _detail(self, record: TaskRecord) -> TaskDetail:
        if record.action_id is None:
            raise Phase7ApiError(
                409,
                "TASK_PREPARATION_PENDING",
                "The task is not ready for browser display.",
            )
        with self._sessions() as session:
            row = session.execute(
                select(
                    PreparedActionORM,
                    ActionLifecycleORM,
                    ApprovalRecordORM,
                    OperationRecordORM,
                )
                .join(
                    ActionLifecycleORM,
                    ActionLifecycleORM.action_id == PreparedActionORM.action_id,
                )
                .outerjoin(
                    ApprovalRecordORM,
                    ApprovalRecordORM.action_id == PreparedActionORM.action_id,
                )
                .outerjoin(
                    OperationRecordORM,
                    OperationRecordORM.action_id == PreparedActionORM.action_id,
                )
                .where(PreparedActionORM.action_id == record.action_id.value)
            ).first()
            if row is None:
                raise Phase7ApiError(500, "INTERNAL_ERROR", "AAOS could not complete the request.")
            prepared_row, lifecycle, approval_row, operation = row
            prepared = _prepared_from_orm(prepared_row)
            approval = _approval_from_orm(approval_row, prepared) if approval_row else None
            try:
                action_state = ActionState(lifecycle.state)
                operation_state = None if operation is None else OperationState(operation.state)
            except ValueError:
                raise Phase7ApiError(
                    500, "INTERNAL_ERROR", "AAOS could not complete the request."
                ) from None
            return self._make_detail(
                record,
                prepared,
                action_state,
                approval,
                operation_state,
                None if operation is None else operation.outcome_kind,
            )

    def _approval_summary(self, row: Sequence[object]) -> ApprovalSummary:
        approval_row, prepared_row, lifecycle, operation, task_row = row
        prepared = _prepared_from_orm(cast(PreparedActionORM, prepared_row))
        approval = _approval_from_orm(cast(ApprovalRecordORM, approval_row), prepared)
        try:
            action_state = ActionState(cast(ActionLifecycleORM, lifecycle).state)
            operation_state = (
                None
                if operation is None
                else OperationState(cast(OperationRecordORM, operation).state)
            )
        except ValueError:
            raise Phase7ApiError(
                500, "INTERNAL_ERROR", "AAOS could not complete the request."
            ) from None
        task = cast(TaskRecordORM, task_row)
        return ApprovalSummary(
            approval_id=approval.approval_id.value,
            task_id=task.task_id,
            action=_safe_action(prepared),
            status=approval.status,
            issued_at=approval.issued_at,
            approval_expires_at=approval.approval_expires_at,
            operation_state=operation_state,
            can_approve=approval.status is ApprovalState.PENDING
            and action_state is ActionState.AWAITING_APPROVAL,
            can_reject=approval.status is ApprovalState.PENDING
            and action_state is ActionState.AWAITING_APPROVAL,
        )

    @staticmethod
    def _summary(detail: TaskDetail) -> TaskSummary:
        return TaskSummary(
            task_id=detail.task_id,
            task_kind=detail.task_kind,
            status=detail.status,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            action=detail.action,
        )

    @staticmethod
    def _make_detail(
        record: TaskRecord,
        prepared: object,
        action_state: ActionState,
        approval: object,
        operation_state: OperationState | None,
        outcome: str | None,
    ) -> TaskDetail:
        prepared_action = cast(PreparedAction, prepared)
        approval_record = cast(ApprovalRecord | None, approval)
        safe_action = _safe_action(prepared_action)
        approval_summary = None
        if approval_record is not None:
            approval_summary = ApprovalSummary(
                approval_id=approval_record.approval_id.value,
                task_id=record.task_id,
                action=safe_action,
                status=approval_record.status,
                issued_at=approval_record.issued_at,
                approval_expires_at=approval_record.approval_expires_at,
                operation_state=operation_state,
                can_approve=approval_record.status is ApprovalState.PENDING
                and action_state is ActionState.AWAITING_APPROVAL,
                can_reject=approval_record.status is ApprovalState.PENDING
                and action_state is ActionState.AWAITING_APPROVAL,
            )
        return TaskDetail(
            task_id=record.task_id,
            task_kind=record.task_kind,
            status=project_task_state(
                record.creation_state,
                action_state,
                None if approval_record is None else approval_record.status,
                operation_state,
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            action=safe_action,
            approval=approval_summary,
            operation_state=operation_state,
            operation_outcome=outcome,
            action_expires_at=prepared_action.action_expires_at,
            failure_code=record.failure_code,
        )


__all__ = ["TaskReadService"]
