"""Task 7 tests for the fake adapter and durable dispatch fence."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from test_phase6a_service import APPROVER, NOW, fake_definition, policy, prepare

from aaos.agent.contracts import PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import (
    FakeExternalAdapter,
    dispatch_claimed_operation,
    load_executable_action,
)
from aaos.approval.contracts import (
    AdapterOutcomeKind,
    ClaimIdentity,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    RemotePrecondition,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import approve_approval, claim_action, request_approval
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import ActionEventORM, OperationRecordORM
from aaos.persistence.database import create_database_engine, create_session_factory


class DispatchContext(TypedDict):
    capability_policy: PermissionPolicy
    definition: Phase6ActionDefinition
    now: datetime


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_database_engine(tmp_path / "phase6a-adapter.db")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> ApprovalRepository:
    return ApprovalRepository(sessions)


def _claimed(repo: ApprovalRepository) -> tuple[OperationId, RemotePrecondition]:
    action_id = prepare(repo)
    approval = request_approval(repo, action_id, APPROVER, NOW + timedelta(minutes=5), NOW)
    approved = approve_approval(repo, approval.approval_id, APPROVER, NOW + timedelta(seconds=1))
    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker"),
    )
    assert result.claimed and result.operation_id is not None
    return result.operation_id, RemotePrecondition("fake", "resource-1", "v1", "etag-1")


def _dispatch_context() -> DispatchContext:
    return {
        "capability_policy": policy(PermissionClass.LOCAL_STATE_WRITE),
        "definition": fake_definition(),
        "now": NOW + timedelta(seconds=3),
    }


def test_fake_adapter_enforces_precondition_at_side_effect_boundary(
    repo: ApprovalRepository,
) -> None:
    operation_id, precondition = _claimed(repo)
    adapter = FakeExternalAdapter(precondition)
    executable = load_executable_action(repo, operation_id)
    assert adapter.check_precondition(executable)
    adapter.mutate_remote_state()

    result = dispatch_claimed_operation(repo, adapter, operation_id, **_dispatch_context())

    assert result.adapter_invoked is True
    assert result.dispatch_started is True
    assert result.adapter_outcome is not None
    assert result.adapter_outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE
    assert adapter.side_effect_count == 0


def test_dispatch_starts_durably_before_adapter_execute(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
) -> None:
    operation_id, precondition = _claimed(repo)
    observed: list[OperationState] = []

    class ObservingAdapter(FakeExternalAdapter):
        def execute(self, action):  # type: ignore[no-untyped-def]
            with sessions() as session:
                operation = session.get(OperationRecordORM, action.operation_id.value)
                assert operation is not None
                observed.append(OperationState(operation.state))
            return super().execute(action)

    observing = ObservingAdapter(precondition)
    result = dispatch_claimed_operation(repo, observing, operation_id, **_dispatch_context())

    assert result.adapter_invoked is True
    assert observed == [OperationState.EXECUTING]
    with sessions() as session:
        operation = session.get(OperationRecordORM, operation_id.value)
        assert operation is not None
        assert operation.state == OperationState.EXECUTING.value
        assert operation.dispatch_started_at is not None


def test_concurrent_dispatch_invokes_adapter_once(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
) -> None:
    operation_id, precondition = _claimed(repo)
    adapter = FakeExternalAdapter(precondition)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                lambda _: dispatch_claimed_operation(
                    repo, adapter, operation_id, **_dispatch_context()
                ),
                range(2),
            )
        )

    assert sum(result.adapter_invoked for result in results) == 1
    with sessions() as session:
        events = session.scalars(
            select(ActionEventORM).where(ActionEventORM.event_kind == "DISPATCH_STARTED")
        ).all()
    assert len(events) == 1
    assert adapter.execute_count == 1


def test_executable_projection_contains_only_least_authority_fields(
    repo: ApprovalRepository,
) -> None:
    operation_id, _ = _claimed(repo)
    executable = load_executable_action(repo, operation_id)
    assert set(executable.to_dict()) == {
        "target_identity",
        "action_type",
        "canonical_arguments",
        "operation_id",
        "remote_precondition",
    }
    assert not hasattr(executable, "approval")
    assert not hasattr(executable, "policy")
    assert not hasattr(executable, "session")


def test_dispatch_rejects_non_claimed_operation_without_adapter_call(
    repo: ApprovalRepository,
) -> None:
    operation_id, precondition = _claimed(repo)
    adapter = FakeExternalAdapter(precondition)
    first = dispatch_claimed_operation(repo, adapter, operation_id, **_dispatch_context())
    second = dispatch_claimed_operation(repo, adapter, operation_id, **_dispatch_context())
    assert first.adapter_invoked is True
    assert second.adapter_invoked is False
    assert second.dispatch_started is False


def test_unknown_operation_is_safe_and_does_not_invoke_adapter(
    repo: ApprovalRepository,
) -> None:
    adapter = FakeExternalAdapter(RemotePrecondition("fake", "resource-1", "v1", "etag-1"))
    result = dispatch_claimed_operation(
        repo,
        adapter,
        OperationId("missing-operation"),
        **_dispatch_context(),
    )
    assert result.adapter_invoked is False
    assert result.dispatch_started is False
    assert result.error_code is not None
