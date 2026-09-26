"""TDD coverage for trusted Phase 7E task creation and Phase 6 binding."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import JSONValue, PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.contracts import (
    ActionType,
    ActorIdentity,
    ApprovalRecord,
    ApprovalState,
    Phase6ActionDefinition,
    PreparedAction,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    GoogleCalendarConfig,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import ApprovalRecordORM, PreparedActionORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.task_models import TaskRecordORM
from aaos.tasks.contracts import TaskCreationState, TaskFailureCode
from aaos.tasks.repository import TaskRepository
from aaos.tasks.service import TaskApplicationService, TaskPreparationError

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
ACTOR = ActorIdentity("local-user")
APPROVER = TrustedApproverContext("local-user", "local-context")


def _request(*, summary: str = "Planning") -> CalendarCreateRequest:
    return CalendarCreateRequest(
        summary=summary,
        start=NOW + timedelta(hours=2),
        end=NOW + timedelta(hours=3),
        timezone="UTC",
        description="Bounded task",
        location="Room 6B",
    )


def _config() -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id="phase7e-calendar",
        expected_service_identity="phase7e-service",
        oauth_scope="https://www.googleapis.com/auth/calendar.events",
        credential_path="C:/trusted/not-opened.json",
        provider_adapter_version="phase6b-v1",
        enabled=True,
    )


def _definition() -> Phase6ActionDefinition:
    def validate(arguments: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
        expected = {"summary", "start", "end", "timezone", "description", "location"}
        if set(arguments) != expected:
            raise ValueError("calendar arguments do not match the closed schema")
        for name in ("summary", "start", "end", "timezone"):
            if not isinstance(arguments[name], str):
                raise TypeError(f"{name} must be a string")
        for name in ("description", "location"):
            if arguments[name] is not None and not isinstance(arguments[name], str):
                raise TypeError(f"{name} must be a string or null")
        return dict(arguments)

    return Phase6ActionDefinition(
        action_type=ActionType("CREATE_EVENT"),
        tool_name="calendar_create_event",
        tool_schema_version="phase6b-calendar-v1",
        tool_definition_fingerprint="6" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase5-v1",
        policy_fingerprint="7" * 64,
        timeout_seconds=10,
        argument_validator=validate,
    )


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(tmp_path / "phase7e-task-service.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def service(sessions: sessionmaker[Session]) -> TaskApplicationService:
    return TaskApplicationService(
        TaskRepository(sessions),
        ApprovalRepository(sessions),
        definition=_definition(),
        capability_policy=PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
        actor_identity=ACTOR,
        approver=APPROVER,
        calendar_config=_config(),
    )


def test_valid_creation_binds_one_action_and_one_pending_approval(
    service: TaskApplicationService, sessions: sessionmaker[Session]
) -> None:
    detail = service.create_calendar_task(_request(), idempotency_key="task-1", now=NOW)

    assert detail.task_kind.value == "CALENDAR_CREATE_EVENT"
    assert detail.status.value == "AWAITING_APPROVAL"
    assert detail.approval is not None
    assert detail.approval.status.value == ApprovalState.PENDING.value
    assert detail.action.summary == "Planning"
    with sessions() as session:
        prepared = session.scalar(select(PreparedActionORM))
        approval = session.scalar(select(ApprovalRecordORM))
        assert prepared is not None
        assert prepared.actor_identity == ACTOR.value
        assert prepared.action_type == "CREATE_EVENT"
        assert approval is not None
        assert approval.approver_identity == APPROVER.approver_identity.value
        assert approval.approver_context_id == APPROVER.context_id
        assert session.scalar(select(func.count()).select_from(PreparedActionORM)) == 1
        assert session.scalar(select(func.count()).select_from(ApprovalRecordORM)) == 1


def test_idempotent_replay_returns_same_task_without_duplicate_binding(
    service: TaskApplicationService, sessions: sessionmaker[Session]
) -> None:
    first = service.create_calendar_task(_request(), idempotency_key="task-2", now=NOW)
    second = service.create_calendar_task(_request(), idempotency_key="task-2", now=NOW)

    assert second.task_id == first.task_id
    assert second.approval is not None and first.approval is not None
    assert second.approval.approval_id == first.approval.approval_id
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PreparedActionORM)) == 1
        assert session.scalar(select(func.count()).select_from(ApprovalRecordORM)) == 1


def test_conflicting_idempotency_fingerprint_is_rejected(service: TaskApplicationService) -> None:
    service.create_calendar_task(_request(summary="one"), idempotency_key="task-3", now=NOW)
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        service.create_calendar_task(_request(summary="two"), idempotency_key="task-3", now=NOW)


def test_capability_denial_is_bounded_and_does_not_prepare_action(
    sessions: sessionmaker[Session],
) -> None:
    denied = TaskApplicationService(
        TaskRepository(sessions),
        ApprovalRepository(sessions),
        definition=_definition(),
        capability_policy=PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
        actor_identity=ACTOR,
        approver=APPROVER,
        calendar_config=_config(),
    )
    with pytest.raises(TaskPreparationError) as error:
        denied.create_calendar_task(_request(), idempotency_key="task-4", now=NOW)
    assert error.value.code is TaskFailureCode.CAPABILITY_DENIED
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PreparedActionORM)) == 0
        task = session.get(TaskRecordORM, error.value.task_id)
        assert task is not None
        assert task.creation_state == TaskCreationState.FAILED.value
        assert task.failure_code == TaskFailureCode.CAPABILITY_DENIED.value


def test_invalid_calendar_request_is_bounded_without_adapter_or_phase6_execution(
    service: TaskApplicationService,
) -> None:
    invalid = CalendarCreateRequest(
        summary="invalid",
        start=NOW - timedelta(hours=1),
        end=NOW,
        timezone="UTC",
    )
    with pytest.raises(TaskPreparationError) as error:
        service.create_calendar_task(invalid, idempotency_key="task-5", now=NOW)
    assert error.value.code is TaskFailureCode.INVALID_REQUEST


def test_preparation_failure_leaves_task_in_created_preparing_state(
    service: TaskApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_prepare_action(*, request: CalendarCreateRequest, now: datetime) -> NoReturn:
        del request, now
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_prepare_action", fail_prepare_action)
    with pytest.raises(TaskPreparationError) as error:
        service.create_calendar_task(_request(), idempotency_key="task-6", now=NOW)
    assert error.value.code is TaskFailureCode.PREPARATION_FAILED
    record = service._task_repository.get_by_idempotency(ACTOR, "task-6")
    assert record is not None
    assert record.creation_state is TaskCreationState.CREATED


def test_recovery_after_action_linkage_issues_one_existing_approval(
    service: TaskApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = service._issue_approval
    calls = 0

    def fail_once(*, prepared: PreparedAction, now: datetime) -> ApprovalRecord:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("approval issuance interrupted")
        return original(prepared=prepared, now=now)

    monkeypatch.setattr(service, "_issue_approval", fail_once)
    with pytest.raises(TaskPreparationError):
        service.create_calendar_task(_request(), idempotency_key="task-7", now=NOW)

    monkeypatch.setattr(service, "_issue_approval", original)
    recovered = service.create_calendar_task(_request(), idempotency_key="task-7", now=NOW)
    assert recovered.approval is not None
    with service._approval_repository.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRecordORM)) == 1


def test_task_creation_never_calls_external_adapter(service: TaskApplicationService) -> None:
    assert not hasattr(service, "adapter")
