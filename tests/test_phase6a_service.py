"""Trusted preparation and human approval service tests for Phase 6A."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn, cast

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
    ClaimIdentity,
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
    load_claim_snapshot,
    read_action_event_history,
)
from aaos.approval.service import (
    ClaimDecision,
    ClaimResult,
    Phase6ServiceError,
    approve_approval,
    claim_action,
    invalidate_for_trusted_drift,
    prepare_action,
    reject_approval,
    request_approval,
    revoke_approval,
    validate_claim_snapshot,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import (
    ActionEventORM,
    ActionLifecycleORM,
    ApprovalConsumptionORM,
    ApprovalRecordORM,
    OperationRecordORM,
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


def definition_variant(
    *,
    action_type: ActionType | None = None,
    schema_version: str = "v1",
    definition_fingerprint: str = "2" * 64,
    policy_version: str = "phase5-v1",
    policy_fingerprint: str = "3" * 64,
    validator: Callable[[Mapping[str, JSONValue]], Mapping[str, JSONValue]] | None = None,
) -> Phase6ActionDefinition:
    base = fake_definition()
    return Phase6ActionDefinition(
        action_type=action_type or base.action_type,
        tool_name=base.tool_name,
        tool_schema_version=schema_version,
        tool_definition_fingerprint=definition_fingerprint,
        required_permission=base.required_permission,
        policy_version=policy_version,
        policy_fingerprint=policy_fingerprint,
        timeout_seconds=base.timeout_seconds,
        argument_validator=validator or base.validate_arguments,
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


def test_claim_action_consumes_approval_and_creates_claimed_operation(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.claimed is True
    assert result.action_id == approved.action_id
    assert result.operation_id == approved.operation_id
    assert result.error_code is None
    with sessions() as session:
        consumption = session.get(ApprovalConsumptionORM, approval_id.value)
        operation = session.get(OperationRecordORM, approved.operation_id.value)
        lifecycle = session.get(ActionLifecycleORM, approved.action_id.value)
        assert consumption is not None
        assert operation is not None
        assert lifecycle is not None
        assert consumption.claim_identity == "claim-worker-1"
        assert operation.state == "CLAIMED"
        assert lifecycle.state == ActionState.CLAIMED.value


def test_claim_rechecks_capability_and_invalidates_before_consumption(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))

    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.READ_ONLY),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result == ClaimResult(approved.action_id, None, False, Phase6ErrorCode.CAPABILITY_DENIED)
    assert stored_states(sessions, approval_id) == (
        ApprovalState.REVOKED,
        ActionState.INVALIDATED,
        TrustedInvalidationReason.CAPABILITY_REVOKED.value,
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalConsumptionORM)) == 0
        assert session.scalar(select(func.count()).select_from(OperationRecordORM)) == 0


def test_claim_expiry_coherently_expires_approval_and_action(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(
        repo,
        action_expires_at=NOW + timedelta(minutes=2),
        approval_expires_at=NOW + timedelta(minutes=1),
    )
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))

    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(minutes=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.error_code is Phase6ErrorCode.APPROVAL_EXPIRED
    assert result.claimed is False
    assert stored_states(sessions, approval_id) == (
        ApprovalState.EXPIRED,
        ActionState.EXPIRED,
        TrustedInvalidationReason.ACTION_EXPIRED.value,
    )


def test_claim_rejects_human_revocation_without_creating_operation(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    revoke_approval(
        repo,
        approval_id,
        APPROVER,
        NOW + timedelta(seconds=2),
        HumanDecisionReason.USER_REVOKED,
    )

    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=3),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result == ClaimResult(approved.action_id, None, False, Phase6ErrorCode.APPROVAL_REVOKED)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationRecordORM)) == 0


def test_claim_is_one_shot_and_duplicate_claim_is_a_safe_conflict(
    repo: ApprovalRepository,
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    first = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )
    second = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-2"),
    )

    assert first.claimed is True
    assert second.claimed is False
    assert second.error_code is Phase6ErrorCode.CLAIM_CONFLICT


def test_claim_event_failure_rolls_back_consumption_and_all_state_changes(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    with sessions.begin() as session:
        session.execute(
            text(
                "CREATE TRIGGER fail_claim_event BEFORE INSERT ON action_events "
                "WHEN NEW.event_kind = 'APPROVAL_CONSUMED' "
                "BEGIN SELECT RAISE(ABORT, 'forced'); END"
            )
        )

    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.claimed is False
    assert result.error_code is Phase6ErrorCode.PERSISTENCE_CONFLICT
    assert stored_states(sessions, approval_id) == (
        ApprovalState.APPROVED,
        ActionState.APPROVED,
        HumanDecisionReason.USER_APPROVED.value,
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalConsumptionORM)) == 0
        assert session.scalar(select(func.count()).select_from(OperationRecordORM)) == 0


def test_independent_connections_have_exactly_one_claim_winner(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))

    def run(worker: str) -> ClaimResult:
        return claim_action(
            repo,
            approved.action_id,
            capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
            definition=fake_definition(),
            now=NOW + timedelta(seconds=2),
            claim_identity=ClaimIdentity(worker),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ("claim-worker-1", "claim-worker-2")))

    assert [result.claimed for result in results].count(True) == 1
    assert [result.claimed for result in results].count(False) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalConsumptionORM)) == 1
        assert session.scalar(select(func.count()).select_from(OperationRecordORM)) == 1


@pytest.mark.parametrize(
    ("definition", "reason"),
    [
        (definition_variant(schema_version="v2"), TrustedInvalidationReason.SCHEMA_VERSION_DRIFT),
        (
            definition_variant(policy_fingerprint="4" * 64),
            TrustedInvalidationReason.POLICY_VERSION_DRIFT,
        ),
        (
            definition_variant(action_type=ActionType("fake.update")),
            TrustedInvalidationReason.BINDING_DRIFT,
        ),
    ],
)
def test_claim_persists_exact_static_drift_reason(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
    definition: Phase6ActionDefinition,
    reason: TrustedInvalidationReason,
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=definition,
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.error_code is Phase6ErrorCode.ACTION_INVALIDATED
    assert stored_states(sessions, approval_id)[2] == reason.value


def test_validator_rejection_is_persisted_as_schema_drift(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))

    def rejects(_: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
        raise ValueError("schema changed")

    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=definition_variant(validator=rejects),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.error_code is Phase6ErrorCode.ACTION_INVALIDATED
    assert (
        stored_states(sessions, approval_id)[2]
        == TrustedInvalidationReason.SCHEMA_VERSION_DRIFT.value
    )


def test_inconsistent_snapshot_detects_stored_precondition_without_mutating_immutable_row(
    repo: ApprovalRepository, sessions: sessionmaker[Session]
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    with sessions.begin() as session:
        snapshot = load_claim_snapshot(session, approved.action_id)
    altered = replace(
        snapshot, stored_precondition=RemotePrecondition("fake", "resource-1", "v1", "etag-2")
    )
    decision = validate_claim_snapshot(
        altered,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
    )

    assert decision.invalidates is True
    assert decision.error_code is Phase6ErrorCode.ACTION_INVALIDATED
    with sessions() as session:
        stored = session.get(PreparedActionORM, approved.action_id.value)
        assert stored is not None
        assert (
            stored.remote_precondition
            == '{"etag":"etag-1","provider":"fake","resource":"resource-1","version":"v1"}'
        )


def test_claim_rejects_identity_and_hash_mismatches_as_binding_drift(
    repo: ApprovalRepository,
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    with repo.transaction() as session:
        snapshot = load_claim_snapshot(session, approved.action_id)
    mismatched_identity = replace(snapshot.approval, action_id=ActionId("other-action"))
    mismatched_hash = replace(
        snapshot.approval,
        prepared_action_sha256="f" * 64,
        approval_binding_sha256=compute_approval_binding_sha256(
            "f" * 64, APPROVER, snapshot.approval.approval_expires_at
        ),
    )
    for approval in (mismatched_identity, mismatched_hash):
        decision = validate_claim_snapshot(
            replace(snapshot, approval=approval),
            capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
            definition=fake_definition(),
            now=NOW + timedelta(seconds=2),
        )
        assert decision == ClaimDecision(True, Phase6ErrorCode.ACTION_INVALIDATED)


@pytest.mark.parametrize(
    "failing_method",
    (
        "insert_consumption",
        "mark_approval_consumed",
        "cas_claimed_and_create_operation",
        "append_claim_event",
    ),
)
def test_claim_rolls_back_after_each_authority_step_failure(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    failing_method: str,
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise PersistenceConflict()

    monkeypatch.setattr(repo, failing_method, fail)
    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.claimed is False
    assert result.error_code is Phase6ErrorCode.PERSISTENCE_CONFLICT
    assert stored_states(sessions, approval_id)[:2] == (
        ApprovalState.APPROVED,
        ActionState.APPROVED,
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalConsumptionORM)) == 0
        assert session.scalar(select(func.count()).select_from(OperationRecordORM)) == 0


def test_claim_has_no_dispatch_surface_or_live_io(
    repo: ApprovalRepository, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    approval_id = issue(repo)
    approved = approve_approval(repo, approval_id, APPROVER, NOW + timedelta(seconds=1))
    calls: list[str] = []
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: calls.append("socket"))
    result = claim_action(
        repo,
        approved.action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=fake_definition(),
        now=NOW + timedelta(seconds=2),
        claim_identity=ClaimIdentity("claim-worker-1"),
    )

    assert result.claimed is True
    assert calls == []
    assert not hasattr(__import__("aaos.approval.service", fromlist=["service"]), "dispatch_action")
    with sessions() as session:
        operation = session.get(OperationRecordORM, approved.operation_id.value)
        assert operation is not None and operation.state == "CLAIMED"
