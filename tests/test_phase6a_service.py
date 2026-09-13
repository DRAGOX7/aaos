"""Trusted preparation and human approval service tests for Phase 6A."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import JSONValue, PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ActionType,
    ActorIdentity,
    ApprovalDecision,
    ApprovalId,
    ApprovalState,
    HumanDecisionReason,
    Phase6ActionDefinition,
    Phase6ErrorCode,
    PrepareActionRequest,
    RemotePrecondition,
    TargetIdentity,
    TrustedApproverContext,
    TrustedInvalidationReason,
    compute_approval_binding_sha256,
)
from aaos.approval.repository import (
    ApprovalRepository,
    PersistenceConflict,
    read_action_event_history,
)
from aaos.approval.service import (
    Phase6ServiceError,
    approve_approval,
    invalidate_for_trusted_drift,
    prepare_action,
    reject_approval,
    request_approval,
    revoke_approval,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import (
    ActionEventORM,
    ActionLifecycleORM,
    ApprovalRecordORM,
    PreparedActionORM,
)
from aaos.persistence.database import create_database_engine, create_session_factory

UTC = timezone.utc
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
ACTOR = ActorIdentity("actor-1")
TARGET = TargetIdentity("fake", "account-1", "resource-1")
PRECONDITION = RemotePrecondition("fake", "resource-1", "v1", "etag-1")
APPROVER = TrustedApproverContext("approver-1", "context-1")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database_engine = create_database_engine(tmp_path / "phase6a-service.db")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def repo(sessions: sessionmaker[Session]) -> ApprovalRepository:
    return ApprovalRepository(sessions)


def fake_definition() -> Phase6ActionDefinition:
    def validate(arguments: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
        if set(arguments) != {"title"} or not isinstance(arguments["title"], str):
            raise ValueError("invalid fake action arguments")
        return {"title": arguments["title"]}

    return Phase6ActionDefinition(
        action_type=ActionType("fake.create"),
        tool_name="write_task_state",
        tool_schema_version="v1",
        tool_definition_fingerprint="2" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase5-v1",
        policy_fingerprint="3" * 64,
        timeout_seconds=30,
        argument_validator=validate,
    )


def policy(*permissions: PermissionClass) -> PermissionPolicy:
    return PermissionPolicy.from_values(permissions)


def prepare(
    repo: ApprovalRepository,
    *,
    now: datetime = NOW,
    action_expires_at: datetime = NOW + timedelta(minutes=10),
) -> ActionId:
    prepared = prepare_action(
        repo,
        fake_definition(),
        actor_identity=ACTOR,
        target_identity=TARGET,
        arguments={"title": "bounded"},
        remote_precondition=PRECONDITION,
        action_expires_at=action_expires_at,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        now=now,
    )
    return prepared.action_id


def issue(
    repo: ApprovalRepository,
    *,
    now: datetime = NOW,
    action_expires_at: datetime = NOW + timedelta(minutes=10),
    approval_expires_at: datetime = NOW + timedelta(minutes=5),
) -> ApprovalId:
    action_id = prepare(repo, now=now, action_expires_at=action_expires_at)
    return request_approval(repo, action_id, APPROVER, approval_expires_at, now).approval_id


def counts(sessions: sessionmaker[Session]) -> tuple[int, int, int, int]:
    with sessions() as session:
        return (
            session.scalar(select(func.count()).select_from(PreparedActionORM)) or 0,
            session.scalar(select(func.count()).select_from(ActionLifecycleORM)) or 0,
            session.scalar(select(func.count()).select_from(ApprovalRecordORM)) or 0,
            session.scalar(select(func.count()).select_from(ActionEventORM)) or 0,
        )


def stored_states(
    sessions: sessionmaker[Session], approval_id: ApprovalId
) -> tuple[ApprovalState, ActionState, str | None]:
    with sessions() as session:
        approval = session.get(ApprovalRecordORM, approval_id.value)
        assert approval is not None
        lifecycle = session.get(ActionLifecycleORM, approval.action_id)
        assert lifecycle is not None
        return ApprovalState(approval.status), ActionState(lifecycle.state), approval.reason_code


def test_prepare_denies_missing_capability_without_creating_action(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    with pytest.raises(Phase6ServiceError) as error:
        prepare_action(
            repo,
            fake_definition(),
            actor_identity=ACTOR,
            target_identity=TARGET,
            arguments={"title": "bounded"},
            remote_precondition=PRECONDITION,
            action_expires_at=NOW + timedelta(minutes=10),
            capability_policy=policy(PermissionClass.READ_ONLY),
            now=NOW,
        )
    assert error.value.code is Phase6ErrorCode.CAPABILITY_DENIED
    assert counts(sessions) == (0, 0, 0, 0)


def test_prepare_uses_real_phase5_policy_and_persists_only_prepared_state(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    prepared = prepare_action(
        repo,
        fake_definition(),
        actor_identity=ACTOR,
        target_identity=TARGET,
        arguments={"title": "bounded"},
        remote_precondition=PRECONDITION,
        action_expires_at=NOW + timedelta(minutes=10),
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        now=NOW,
    )

    with sessions() as session:
        stored = session.get(PreparedActionORM, prepared.action_id.value)
        lifecycle = session.get(ActionLifecycleORM, prepared.action_id.value)
        assert stored is not None
        assert lifecycle is not None
        assert stored.prepared_action_sha256 == prepared.prepared_action_sha256
        assert stored.canonical_arguments == b'{"title":"bounded"}'
        assert lifecycle.state == ActionState.PREPARED.value
        assert lifecycle.revision == 0
        history = read_action_event_history(session, prepared.action_id)
    assert [event.event_kind.value for event in history] == ["ACTION_PREPARED"]


def test_prepare_rejects_invalid_schema_before_persistence(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    with pytest.raises(ValueError, match="invalid fake action arguments"):
        prepare_action(
            repo,
            fake_definition(),
            actor_identity=ACTOR,
            target_identity=TARGET,
            arguments=cast(Mapping[str, JSONValue], {"title": "x", "permission": "grant"}),
            remote_precondition=PRECONDITION,
            action_expires_at=NOW + timedelta(minutes=10),
            capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
            now=NOW,
        )
    assert counts(sessions) == (0, 0, 0, 0)


def test_task5_request_dtos_are_closed_and_trusted_context_has_no_untrusted_parser() -> None:
    request = PrepareActionRequest(
        actor_identity=ACTOR,
        target_identity=TARGET,
        arguments={"title": "bounded"},
        remote_precondition=PRECONDITION,
        action_expires_at=NOW + timedelta(minutes=10),
    )
    decision = ApprovalDecision(
        approval_id=ApprovalId("approval-1"),
        approver=APPROVER,
        reason=HumanDecisionReason.USER_REJECTED,
        decided_at=NOW,
    )
    assert request.actor_identity is ACTOR
    assert decision.approver is APPROVER
    assert not hasattr(TrustedApproverContext, "from_untrusted_mapping")
    with pytest.raises(TypeError):
        PrepareActionRequest(
            actor_identity=ACTOR,
            target_identity=TARGET,
            arguments={"title": "bounded"},
            remote_precondition=PRECONDITION,
            action_expires_at=NOW + timedelta(minutes=10),
            permission="grant",  # type: ignore[call-arg]
        )


def test_request_approval_creates_pending_and_moves_action_to_awaiting(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    action_id = prepare(repo)
    approval = request_approval(repo, action_id, APPROVER, NOW + timedelta(minutes=5), NOW)

    assert approval.status is ApprovalState.PENDING
    assert approval.approver == APPROVER
    assert approval.approval_binding_sha256
    assert stored_states(sessions, approval.approval_id) == (
        ApprovalState.PENDING,
        ActionState.AWAITING_APPROVAL,
        None,
    )
    with sessions() as session:
        history = read_action_event_history(session, action_id)
    assert [event.event_kind.value for event in history] == [
        "ACTION_PREPARED",
        "APPROVAL_REQUESTED",
    ]


@pytest.mark.parametrize(
    "approval_expires_at",
    (NOW, NOW - timedelta(microseconds=1), NOW + timedelta(minutes=11)),
)
def test_request_approval_rejects_nonpositive_or_out_of_order_expiry_atomically(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
    approval_expires_at: datetime,
) -> None:
    action_id = prepare(repo)
    before = counts(sessions)
    with pytest.raises(Phase6ServiceError) as error:
        request_approval(repo, action_id, APPROVER, approval_expires_at, NOW)
    assert error.value.code is Phase6ErrorCode.APPROVAL_EXPIRED
    assert counts(sessions) == before
    with sessions() as session:
        lifecycle = session.get(ActionLifecycleORM, action_id.value)
        assert lifecycle is not None
        assert lifecycle.state == ActionState.PREPARED.value


def test_request_approval_rolls_back_record_and_state_when_event_insert_fails(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    action_id = prepare(repo)
    with sessions.begin() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_requested_event BEFORE INSERT ON action_events "
                "WHEN NEW.event_kind = 'APPROVAL_REQUESTED' "
                "BEGIN SELECT RAISE(ABORT, 'forced'); END"
            )
        )

    with pytest.raises(PersistenceConflict):
        request_approval(repo, action_id, APPROVER, NOW + timedelta(minutes=5), NOW)

    assert counts(sessions) == (1, 1, 0, 1)
    with sessions() as session:
        lifecycle = session.get(ActionLifecycleORM, action_id.value)
        assert lifecycle is not None
        assert (lifecycle.state, lifecycle.revision) == (ActionState.PREPARED.value, 0)


def test_human_approval_requires_exact_bound_identity_and_context(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    for wrong in (
        TrustedApproverContext("other-approver", "context-1"),
        TrustedApproverContext("approver-1", "other-context"),
    ):
        with pytest.raises(Phase6ServiceError) as error:
            approve_approval(repo, approval_id, wrong, NOW + timedelta(seconds=1))
        assert error.value.code is Phase6ErrorCode.APPROVAL_NOT_FOUND
        assert stored_states(sessions, approval_id)[:2] == (
            ApprovalState.PENDING,
            ActionState.AWAITING_APPROVAL,
        )


def test_human_approval_rechecks_hash_against_the_prepared_action(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    other_prepared_hash = "f" * 64
    approval_expires_at = NOW + timedelta(minutes=5)
    with sessions.begin() as session:
        approval = session.get(ApprovalRecordORM, approval_id.value)
        assert approval is not None
        approval.prepared_action_sha256 = other_prepared_hash
        approval.approval_binding_sha256 = compute_approval_binding_sha256(
            other_prepared_hash, APPROVER, approval_expires_at
        )

    with pytest.raises(Phase6ServiceError) as error:
        approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    assert error.value.code is Phase6ErrorCode.VERSION_MISMATCH
    assert stored_states(sessions, approval_id)[:2] == (
        ApprovalState.PENDING,
        ActionState.AWAITING_APPROVAL,
    )


def test_human_approval_moves_bound_records_to_approved(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    assert approved.status is ApprovalState.APPROVED
    assert approved.reason is HumanDecisionReason.USER_APPROVED
    assert stored_states(sessions, approval_id) == (
        ApprovalState.APPROVED,
        ActionState.APPROVED,
        HumanDecisionReason.USER_APPROVED.value,
    )


def test_human_rejection_moves_action_to_rejected(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    rejected = reject_approval(
        repo,
        approval_id,
        APPROVER,
        NOW + timedelta(seconds=1),
        HumanDecisionReason.USER_REJECTED,
    )
    assert rejected.status is ApprovalState.REJECTED
    assert stored_states(sessions, approval_id) == (
        ApprovalState.REJECTED,
        ActionState.REJECTED,
        HumanDecisionReason.USER_REJECTED.value,
    )


@pytest.mark.parametrize("approve_first", (False, True))
def test_human_revocation_moves_pending_or_approved_action_to_revoked(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
    approve_first: bool,
) -> None:
    approval_id = issue(repo)
    if approve_first:
        approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    revoked = revoke_approval(
        repo,
        approval_id,
        APPROVER,
        NOW + timedelta(seconds=2),
        HumanDecisionReason.USER_REVOKED,
    )
    assert revoked.status is ApprovalState.REVOKED
    assert stored_states(sessions, approval_id) == (
        ApprovalState.REVOKED,
        ActionState.REVOKED,
        HumanDecisionReason.USER_REVOKED.value,
    )


def test_human_decision_apis_reject_reason_spoofing(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    with pytest.raises(TypeError):
        reject_approval(
            repo,
            approval_id,
            APPROVER,
            NOW + timedelta(seconds=1),
            cast(HumanDecisionReason, TrustedInvalidationReason.BINDING_DRIFT),
        )
    with pytest.raises(ValueError):
        revoke_approval(
            repo,
            approval_id,
            APPROVER,
            NOW + timedelta(seconds=1),
            HumanDecisionReason.USER_REJECTED,
        )
    assert stored_states(sessions, approval_id)[:2] == (
        ApprovalState.PENDING,
        ActionState.AWAITING_APPROVAL,
    )


def test_trusted_drift_invalidates_approved_action_and_revokes_approval(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    record = invalidate_for_trusted_drift(
        repo,
        approved.action_id,
        TrustedInvalidationReason.BINDING_DRIFT,
        NOW + timedelta(seconds=2),
    )
    assert record.action_state is ActionState.INVALIDATED
    assert stored_states(sessions, approval_id) == (
        ApprovalState.REVOKED,
        ActionState.INVALIDATED,
        TrustedInvalidationReason.BINDING_DRIFT.value,
    )
    with sessions() as session:
        history = read_action_event_history(session, approved.action_id)
    event = history[-1]
    assert event.event_kind.value == "ACTION_INVALIDATED"
    assert event.source.value == "TRUSTED_SYSTEM"
    assert isinstance(event.actor, ActorIdentity)


def test_trusted_drift_rejects_expiry_as_invalidation_reason(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    with pytest.raises(ValueError):
        invalidate_for_trusted_drift(
            repo,
            approved.action_id,
            TrustedInvalidationReason.ACTION_EXPIRED,
            NOW + timedelta(seconds=2),
        )
    assert stored_states(sessions, approval_id)[:2] == (
        ApprovalState.APPROVED,
        ActionState.APPROVED,
    )
