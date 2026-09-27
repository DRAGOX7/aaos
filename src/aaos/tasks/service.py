"""Trusted Phase 7E task creation and Phase 6 approval binding."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import select

from aaos.agent.policy import PermissionPolicy
from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ActorIdentity,
    ApprovalId,
    ApprovalRecord,
    ApprovalState,
    HumanDecisionReason,
    OperationState,
    Phase6ActionDefinition,
    PreparedAction,
    RemotePrecondition,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import (
    Phase6ServiceError,
    _approval_from_orm,
    _prepared_from_orm,
    prepare_action,
    request_approval,
)
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    GoogleCalendarConfig,
    calendar_target,
)
from aaos.persistence.approval_models import (
    ActionLifecycleORM,
    ApprovalRecordORM,
    OperationRecordORM,
    PreparedActionORM,
)
from aaos.tasks.contracts import (
    TaskCreationState,
    TaskFailureCode,
    TaskKind,
    TaskRecord,
    project_task_state,
)
from aaos.tasks.repository import TaskRepository
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalSummary,
    SafeActionSummary,
    TaskDetail,
    _project_approval_state,
    _project_operation_state,
)


class ApprovalReader(Protocol):
    def get_approval(self, approval_id: str) -> ApprovalDetail: ...


class TaskPreparationError(RuntimeError):
    """Bounded failure while creating or recovering a browser task."""

    def __init__(self, code: TaskFailureCode, task_id: str) -> None:
        if not isinstance(code, TaskFailureCode):
            raise TypeError("code must be a TaskFailureCode")
        self.code = code
        self.task_id = task_id
        super().__init__(code.value)


def _request_fingerprint(
    request: CalendarCreateRequest,
    *,
    now: datetime,
    idempotency_scope: str | None = None,
) -> str:
    try:
        canonical = canonicalize_calendar_request(request, now=now)
        value: object = dict(canonical_event_arguments(canonical))
    except (TypeError, ValueError):
        value = {
            "summary": request.summary,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "timezone": request.timezone,
            "description": request.description,
            "location": request.location,
        }
    if idempotency_scope is not None:
        value = {"calendar_request": value, "idempotency_scope": idempotency_scope}
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_action(prepared: PreparedAction) -> SafeActionSummary:
    try:
        values = json.loads(prepared.canonical_arguments.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("stored action arguments are not valid canonical JSON") from error
    if not isinstance(values, dict):
        raise ValueError("stored action arguments must be an object")
    return SafeActionSummary(
        action_type=prepared.action_type.value,
        integration_label="Google Calendar",
        summary=cast(str, values["summary"]),
        start=cast(str, values["start"]),
        end=cast(str, values["end"]),
        timezone=cast(str, values["timezone"]),
        description=cast(str | None, values.get("description")),
        location=cast(str | None, values.get("location")),
    )


class TaskApplicationService:
    """Own browser-task preparation while delegating authority to Phase 6."""

    def __init__(
        self,
        task_repository: TaskRepository,
        approval_repository: ApprovalRepository,
        *,
        definition: Phase6ActionDefinition,
        capability_policy: PermissionPolicy,
        actor_identity: ActorIdentity,
        approver: TrustedApproverContext,
        calendar_config: GoogleCalendarConfig,
        action_lifetime: timedelta = timedelta(minutes=10),
        approval_lifetime: timedelta = timedelta(minutes=5),
    ) -> None:
        if not isinstance(task_repository, TaskRepository):
            raise TypeError("task_repository must be a TaskRepository")
        if not isinstance(approval_repository, ApprovalRepository):
            raise TypeError("approval_repository must be an ApprovalRepository")
        if not isinstance(definition, Phase6ActionDefinition):
            raise TypeError("definition must be a Phase6ActionDefinition")
        if not isinstance(capability_policy, PermissionPolicy):
            raise TypeError("capability_policy must be a PermissionPolicy")
        if not isinstance(actor_identity, ActorIdentity):
            raise TypeError("actor_identity must be an ActorIdentity")
        if not isinstance(approver, TrustedApproverContext):
            raise TypeError("approver must be a TrustedApproverContext")
        if not isinstance(calendar_config, GoogleCalendarConfig):
            raise TypeError("calendar_config must be a GoogleCalendarConfig")
        for name, value in (
            ("action_lifetime", action_lifetime),
            ("approval_lifetime", approval_lifetime),
        ):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        self._task_repository = task_repository
        self._approval_repository = approval_repository
        self._definition = definition
        self._capability_policy = capability_policy
        self._actor_identity = actor_identity
        self._approver = approver
        self._calendar_config = calendar_config
        self._action_lifetime = action_lifetime
        self._approval_lifetime = approval_lifetime

    def create_calendar_task(
        self,
        request: CalendarCreateRequest,
        *,
        idempotency_key: str,
        now: datetime,
        idempotency_scope: str | None = None,
    ) -> TaskDetail:
        if not isinstance(request, CalendarCreateRequest):
            raise TypeError("request must be a CalendarCreateRequest")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency_key must be a nonempty string")
        if idempotency_scope is not None and (
            not isinstance(idempotency_scope, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", idempotency_scope)
        ):
            raise ValueError("idempotency_scope must be a bounded safe identifier")
        fingerprint = _request_fingerprint(
            request,
            now=now,
            idempotency_scope=idempotency_scope,
        )
        candidate = TaskRecord(
            task_id=uuid4().hex,
            task_kind=TaskKind.CALENDAR_CREATE_EVENT,
            actor_identity=self._actor_identity,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            creation_state=TaskCreationState.CREATED,
            failure_code=None,
            action_id=None,
            created_at=now,
            updated_at=now,
        )
        record, created = self._task_repository.create_or_get(candidate)
        if not created:
            return self._recover_existing(record, now=now)

        try:
            prepared = self._prepare_action(request=request, now=now)
        except (TypeError, ValueError):
            self._mark_failed(record, TaskFailureCode.INVALID_REQUEST, now)
            raise TaskPreparationError(TaskFailureCode.INVALID_REQUEST, record.task_id) from None
        except Phase6ServiceError as error:
            code = (
                TaskFailureCode.CAPABILITY_DENIED
                if error.code.value == "CAPABILITY_DENIED"
                else TaskFailureCode.PREPARATION_FAILED
            )
            self._mark_failed(record, code, now)
            raise TaskPreparationError(code, record.task_id) from None
        except Exception:
            raise TaskPreparationError(TaskFailureCode.PREPARATION_FAILED, record.task_id) from None

        bound = self._task_repository.cas_creation_state(
            record.task_id,
            expected=TaskCreationState.CREATED,
            target=TaskCreationState.ACTION_BOUND,
            action_id=prepared.action_id,
            failure_code=None,
            now=now,
        )
        try:
            approval = self._issue_approval(prepared=prepared, now=now)
        except Exception:
            raise TaskPreparationError(TaskFailureCode.PREPARATION_FAILED, bound.task_id) from None
        return self._detail(bound, prepared, ActionState.AWAITING_APPROVAL, approval, None, None)

    def _prepare_action(self, *, request: CalendarCreateRequest, now: datetime) -> PreparedAction:
        canonical = canonicalize_calendar_request(request, now=now)
        target = calendar_target(self._calendar_config)
        return prepare_action(
            self._approval_repository,
            self._definition,
            actor_identity=self._actor_identity,
            target_identity=target,
            arguments=canonical_event_arguments(canonical),
            remote_precondition=RemotePrecondition(
                "google-calendar",
                target.resource,
                self._calendar_config.provider_adapter_version,
                "absent",
            ),
            action_expires_at=now + self._action_lifetime,
            capability_policy=self._capability_policy,
            now=now,
        )

    def _issue_approval(self, *, prepared: PreparedAction, now: datetime) -> ApprovalRecord:
        return request_approval(
            self._approval_repository,
            prepared.action_id,
            self._approver,
            min(now + self._approval_lifetime, prepared.action_expires_at),
            now,
        )

    def _mark_failed(self, record: TaskRecord, code: TaskFailureCode, now: datetime) -> None:
        try:
            self._task_repository.cas_creation_state(
                record.task_id,
                expected=TaskCreationState.CREATED,
                target=TaskCreationState.FAILED,
                action_id=None,
                failure_code=code.value,
                now=now,
            )
        except ValueError:
            pass

    def _recover_existing(self, record: TaskRecord, *, now: datetime) -> TaskDetail:
        if record.creation_state is TaskCreationState.FAILED:
            raise TaskPreparationError(
                record.failure_code or TaskFailureCode.PREPARATION_FAILED, record.task_id
            )
        if record.action_id is None:
            raise TaskPreparationError(TaskFailureCode.PREPARATION_FAILED, record.task_id)
        prepared, action_state, approval, operation_state, outcome = self._load_linked(
            record.action_id
        )
        if approval is None:
            if action_state is not ActionState.PREPARED:
                raise TaskPreparationError(TaskFailureCode.PREPARATION_FAILED, record.task_id)
            approval = self._issue_approval(prepared=prepared, now=now)
            action_state = ActionState.AWAITING_APPROVAL
        return self._detail(record, prepared, action_state, approval, operation_state, outcome)

    def _load_linked(
        self, action_id: ActionId
    ) -> tuple[
        PreparedAction, ActionState, ApprovalRecord | None, OperationState | None, str | None
    ]:
        with self._approval_repository.transaction() as session:
            prepared_row = session.get(PreparedActionORM, action_id.value)
            lifecycle = session.get(ActionLifecycleORM, action_id.value)
            approval_row = session.scalar(
                select(ApprovalRecordORM).where(ApprovalRecordORM.action_id == action_id.value)
            )
            operation = session.scalar(
                select(OperationRecordORM).where(OperationRecordORM.action_id == action_id.value)
            )
            if prepared_row is None or lifecycle is None:
                raise TaskPreparationError(TaskFailureCode.PREPARATION_FAILED, action_id.value)
            prepared = _prepared_from_orm(prepared_row)
            approval = (
                _approval_from_orm(approval_row, prepared) if approval_row is not None else None
            )
            try:
                action_state = ActionState(lifecycle.state)
                operation_state = None if operation is None else OperationState(operation.state)
            except ValueError:
                raise TaskPreparationError(
                    TaskFailureCode.PREPARATION_FAILED, action_id.value
                ) from None
            outcome = None if operation is None else operation.outcome_kind
            return prepared, action_state, approval, operation_state, outcome

    def _detail(
        self,
        record: TaskRecord,
        prepared: PreparedAction,
        action_state: ActionState,
        approval: ApprovalRecord | None,
        operation_state: OperationState | None,
        outcome: str | None,
    ) -> TaskDetail:
        approval_summary = None
        if approval is not None:
            approval_summary = ApprovalSummary(
                approval_id=approval.approval_id.value,
                task_id=record.task_id,
                action=_safe_action(prepared),
                status=_project_approval_state(approval.status),
                issued_at=approval.issued_at,
                approval_expires_at=approval.approval_expires_at,
                operation_state=_project_operation_state(operation_state),
                can_approve=approval.status is ApprovalState.PENDING
                and action_state is ActionState.AWAITING_APPROVAL,
                can_reject=approval.status is ApprovalState.PENDING
                and action_state is ActionState.AWAITING_APPROVAL,
            )
        return TaskDetail(
            task_id=record.task_id,
            task_kind=record.task_kind,
            status=project_task_state(
                record.creation_state,
                action_state,
                approval.status if approval is not None else None,
                operation_state,
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            action=_safe_action(prepared),
            approval=approval_summary,
            operation_state=_project_operation_state(operation_state),
            operation_outcome=outcome,
            action_expires_at=prepared.action_expires_at,
            failure_code=record.failure_code,
        )


class TaskApprovalService:
    """Apply browser approval decisions through the frozen Phase 6 service."""

    def __init__(self, approval_repository: ApprovalRepository, reader: ApprovalReader) -> None:
        if not isinstance(approval_repository, ApprovalRepository):
            raise TypeError("approval_repository must be an ApprovalRepository")
        self._approval_repository = approval_repository
        self._reader = reader

    def approve(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        from aaos.approval.service import Phase6ServiceError, approve_approval

        try:
            approve_approval(self._approval_repository, approval_id, approver, now)
        except Phase6ServiceError as error:
            can_retry = getattr(self._reader, "is_approved_and_unclaimed_by", None)
            if (
                error.code.value != "INVALID_STATE_TRANSITION"
                or not callable(can_retry)
                or not can_retry(approval_id.value, approver=approver)
            ):
                raise
        return self._reader.get_approval(approval_id.value)

    def reject(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        from aaos.approval.service import reject_approval

        reject_approval(
            self._approval_repository,
            approval_id,
            approver,
            now,
            HumanDecisionReason.USER_REJECTED,
        )
        return self._reader.get_approval(approval_id.value)


__all__ = ["TaskApplicationService", "TaskApprovalService", "TaskPreparationError"]
