from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import (
    ActionId,
    ActionState,
    ActorIdentity,
    ApprovalId,
    ClaimIdentity,
    EventKind,
    ExecutionOutcome,
    HumanDecisionReason,
    OperationId,
    OperationState,
    Phase6ErrorCode,
    TrustedApproverContext,
    TrustedInvalidationReason,
)
from aaos.approval.events import (
    ActionEventDetails,
    ActionEventDraft,
    ActionPreparedDetails,
    ApprovalDecisionDetails,
    ApprovalRequestedDetails,
    ClaimedDetails,
    DispatchStartedDetails,
    InvalidatedDetails,
    InvalidEventDetails,
    LateResultIgnoredDetails,
    OutcomeRecordedDetails,
    ReconciliationDetails,
    serialize_event_payload,
)
from aaos.approval.repository import (
    ApprovalRepository,
    PersistenceConflict,
    append_action_event,
    cas_action_lifecycle,
    cas_operation,
    read_action_event_history,
    read_states,
    read_states_and_outcome,
)
from aaos.approval.state import TransitionSource
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import (
    ActionEventCounterORM,
    ActionEventORM,
    ActionLifecycleORM,
    OperationRecordORM,
    PreparedActionORM,
)
from aaos.persistence.database import create_database_engine, create_session_factory

UTC = timezone.utc
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
ACTION_ID = ActionId("action-1")
OPERATION_ID = OperationId("operation-1")
APPROVAL_ID = ApprovalId("approval-1")
ACTOR = ActorIdentity("actor-1")
CLAIM = ClaimIdentity("worker-1")
APPROVER = TrustedApproverContext("approver-1", "context-1")
PREPARED_HASH = "a" * 64
APPROVAL_HASH = "b" * 64


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    database_engine = create_database_engine(tmp_path / "phase6a-repository.db")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


def seed_action(
    sessions: sessionmaker[Session],
    *,
    action_state: ActionState = ActionState.PREPARED,
    action_revision: int = 0,
    operation_state: OperationState | None = None,
    operation_revision: int = 0,
    outcome: ExecutionOutcome | None = None,
) -> None:
    with sessions.begin() as session:
        session.add(
            PreparedActionORM(
                action_id=ACTION_ID.value,
                operation_id=OPERATION_ID.value,
                actor_identity=ACTOR.value,
                target_identity='{"account":"account-1","provider":"fake","resource":"item-1"}',
                action_type="fake.create",
                canonical_arguments=b'{"title":"bounded"}',
                arguments_sha256="1" * 64,
                tool_name="fake_create",
                tool_schema_version="v1",
                tool_definition_fingerprint="2" * 64,
                policy_version="v1",
                policy_fingerprint="3" * 64,
                remote_precondition=(
                    '{"etag":"etag-1","provider":"fake","resource":"item-1","version":"v1"}'
                ),
                prepared_at=NOW,
                action_expires_at=NOW + timedelta(hours=1),
                prepared_action_sha256=PREPARED_HASH,
            )
        )
        session.flush()
        session.add(
            ActionLifecycleORM(
                action_id=ACTION_ID.value,
                state=action_state.value,
                revision=action_revision,
                created_at=NOW,
                updated_at=NOW,
                terminal_at=(NOW if action_state is ActionState.CONFIRMED_SUCCESS else None),
            )
        )
        if operation_state is not None:
            session.add(
                OperationRecordORM(
                    operation_id=OPERATION_ID.value,
                    action_id=ACTION_ID.value,
                    state=operation_state.value,
                    outcome_kind=outcome.value if outcome is not None else None,
                    receipt_evidence=None,
                    dispatch_started_at=None,
                    outcome_recorded_at=None,
                    reconciliation_at=None,
                    revision=operation_revision,
                )
            )


def prepared_event(*, occurred_at: datetime = NOW) -> ActionEventDraft:
    return ActionEventDraft(
        action_id=ACTION_ID,
        operation_id=OPERATION_ID,
        event_kind=EventKind.ACTION_PREPARED,
        source=TransitionSource.TRUSTED_SYSTEM,
        actor=ACTOR,
        occurred_at=occurred_at,
        details=ActionPreparedDetails(
            operation_id=OPERATION_ID,
            prepared_action_sha256=PREPARED_HASH,
        ),
    )


def approval_requested_event(*, occurred_at: datetime = NOW) -> ActionEventDraft:
    return ActionEventDraft(
        action_id=ACTION_ID,
        operation_id=OPERATION_ID,
        event_kind=EventKind.APPROVAL_REQUESTED,
        source=TransitionSource.TRUSTED_SYSTEM,
        actor=ACTOR,
        occurred_at=occurred_at,
        details=ApprovalRequestedDetails(
            approval_id=APPROVAL_ID,
            approval_binding_sha256=APPROVAL_HASH,
            approval_expires_at=NOW + timedelta(minutes=30),
        ),
    )


def outcome_event(*, occurred_at: datetime = NOW + timedelta(minutes=2)) -> ActionEventDraft:
    return ActionEventDraft(
        action_id=ACTION_ID,
        operation_id=OPERATION_ID,
        event_kind=EventKind.OUTCOME_RECORDED,
        source=TransitionSource.DISPATCH_WORKER,
        actor=CLAIM,
        occurred_at=occurred_at,
        details=OutcomeRecordedDetails(
            operation_id=OPERATION_ID,
            outcome=ExecutionOutcome.CONFIRMED_SUCCESS,
            outcome_recorded_at=occurred_at,
        ),
    )


def test_event_sequence_is_transactional_and_monotonic(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(sessions)

    with sessions.begin() as session:
        first = append_action_event(session, prepared_event(), expected_action_revision=0)
        second = append_action_event(
            session,
            approval_requested_event(occurred_at=NOW + timedelta(minutes=1)),
            expected_action_revision=1,
        )

    assert (first.sequence, second.sequence) == (0, 1)
    with sessions() as session:
        assert read_action_event_history(session, ACTION_ID) == (first, second)


def test_failed_event_transaction_rolls_back_revision_and_counter(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(sessions)
    collision = ActionEventORM(
        event_id="event-collision",
        action_id=ACTION_ID.value,
        sequence=0,
        event_kind=EventKind.ACTION_PREPARED.value,
        detail_payload=serialize_event_payload(prepared_event()),
        occurred_at=NOW,
        actor_identity=ACTOR.value,
    )

    with pytest.raises(PersistenceConflict) as error:
        with sessions.begin() as session:
            session.add(collision)
            session.flush()
            append_action_event(session, prepared_event(), expected_action_revision=0)

    assert error.value.code is Phase6ErrorCode.PERSISTENCE_CONFLICT
    with sessions() as session:
        assert read_action_event_history(session, ACTION_ID) == ()
        assert session.get(ActionEventCounterORM, ACTION_ID.value) is None
        lifecycle = session.get(ActionLifecycleORM, ACTION_ID.value)
        assert lifecycle is not None
        assert lifecycle.revision == 0


def test_append_revision_conflict_has_safe_error_and_no_partial_write(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(sessions, action_revision=1)

    with pytest.raises(PersistenceConflict) as error:
        with sessions.begin() as session:
            append_action_event(session, prepared_event(), expected_action_revision=0)

    assert str(error.value) == Phase6ErrorCode.PERSISTENCE_CONFLICT.value
    with sessions() as session:
        assert read_action_event_history(session, ACTION_ID) == ()
        assert session.get(ActionEventCounterORM, ACTION_ID.value) is None


def test_zero_row_action_cas_does_not_overwrite_terminal_state_or_append_event(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(
        sessions,
        action_state=ActionState.CONFIRMED_SUCCESS,
        action_revision=3,
        operation_state=OperationState.CONFIRMED_SUCCESS,
        operation_revision=3,
        outcome=ExecutionOutcome.CONFIRMED_SUCCESS,
    )

    with sessions.begin() as session:
        changed = cas_action_lifecycle(
            session,
            action_id=ACTION_ID,
            expected_state=ActionState.EXECUTING,
            expected_revision=2,
            new_state=ActionState.CONFIRMED_SUCCESS,
            event=outcome_event(),
        )

    assert changed is False
    with sessions() as session:
        assert read_states(session, OPERATION_ID) == (
            ActionState.CONFIRMED_SUCCESS,
            OperationState.CONFIRMED_SUCCESS,
        )
        assert read_action_event_history(session, ACTION_ID) == ()
        assert session.get(ActionEventCounterORM, ACTION_ID.value) is None


def test_successful_action_cas_commits_state_revision_and_event_atomically(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(
        sessions,
        action_state=ActionState.EXECUTING,
        action_revision=2,
        operation_state=OperationState.EXECUTING,
        operation_revision=2,
    )

    with sessions.begin() as session:
        assert cas_action_lifecycle(
            session,
            action_id=ACTION_ID,
            expected_state=ActionState.EXECUTING,
            expected_revision=2,
            new_state=ActionState.CONFIRMED_SUCCESS,
            event=outcome_event(),
        )

    with sessions() as session:
        lifecycle = session.get(ActionLifecycleORM, ACTION_ID.value)
        assert lifecycle is not None
        assert (lifecycle.state, lifecycle.revision, lifecycle.terminal_at) == (
            ActionState.CONFIRMED_SUCCESS.value,
            3,
            NOW + timedelta(minutes=2),
        )
        history = read_action_event_history(session, ACTION_ID)
        assert len(history) == 1
        assert history[0].details == outcome_event().details


def test_operation_cas_uses_state_and_revision_predicates(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(
        sessions,
        action_state=ActionState.CLAIMED,
        action_revision=1,
        operation_state=OperationState.CLAIMED,
        operation_revision=0,
    )

    with sessions.begin() as session:
        assert cas_operation(
            session,
            operation_id=OPERATION_ID,
            expected_state=OperationState.CLAIMED,
            expected_revision=0,
            new_state=OperationState.EXECUTING,
        )
        assert not cas_operation(
            session,
            operation_id=OPERATION_ID,
            expected_state=OperationState.CLAIMED,
            expected_revision=0,
            new_state=OperationState.NOT_DISPATCHED,
        )

    with sessions() as session:
        operation = session.get(OperationRecordORM, OPERATION_ID.value)
        assert operation is not None
        assert (operation.state, operation.revision) == (OperationState.EXECUTING.value, 1)


def test_read_states_and_outcome_returns_closed_enums(
    sessions: sessionmaker[Session],
) -> None:
    seed_action(
        sessions,
        action_state=ActionState.UNKNOWN_OUTCOME,
        action_revision=4,
        operation_state=OperationState.UNKNOWN_OUTCOME,
        operation_revision=4,
        outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
    )

    with sessions() as session:
        assert read_states_and_outcome(session, OPERATION_ID) == (
            ActionState.UNKNOWN_OUTCOME,
            OperationState.UNKNOWN_OUTCOME,
            ExecutionOutcome.UNKNOWN_OUTCOME,
        )


@pytest.mark.parametrize(
    ("event_kind", "details"),
    [
        (
            EventKind.APPROVAL_DECIDED,
            ApprovalDecisionDetails(
                approval_id=APPROVAL_ID,
                reason=HumanDecisionReason.USER_APPROVED,
                decided_at=NOW,
            ),
        ),
        (
            EventKind.APPROVAL_CONSUMED,
            ClaimedDetails(
                approval_id=APPROVAL_ID,
                operation_id=OPERATION_ID,
                claim_identity=CLAIM,
            ),
        ),
        (
            EventKind.DISPATCH_STARTED,
            DispatchStartedDetails(operation_id=OPERATION_ID, dispatch_started_at=NOW),
        ),
        (
            EventKind.RECONCILIATION_RECORDED,
            ReconciliationDetails(
                operation_id=OPERATION_ID,
                outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
                reconciliation_at=NOW,
            ),
        ),
        (
            EventKind.ACTION_INVALIDATED,
            InvalidatedDetails(reason=TrustedInvalidationReason.BINDING_DRIFT),
        ),
        (
            EventKind.LATE_RESULT_IGNORED,
            LateResultIgnoredDetails(
                operation_id=OPERATION_ID,
                reported_outcome=ExecutionOutcome.CONFIRMED_FAILURE,
                reason=Phase6ErrorCode.PERSISTENCE_CONFLICT,
            ),
        ),
    ],
)
def test_closed_event_payload_round_trips_each_actor_and_detail_family(
    sessions: sessionmaker[Session],
    event_kind: EventKind,
    details: ActionEventDetails,
) -> None:
    seed_action(sessions)
    source = TransitionSource.TRUSTED_SYSTEM
    actor: ActorIdentity | ClaimIdentity | TrustedApproverContext = ACTOR
    if event_kind is EventKind.APPROVAL_DECIDED:
        source = TransitionSource.HUMAN_APPROVER
        actor = APPROVER
    elif event_kind in {
        EventKind.APPROVAL_CONSUMED,
        EventKind.DISPATCH_STARTED,
        EventKind.RECONCILIATION_RECORDED,
        EventKind.LATE_RESULT_IGNORED,
    }:
        source = (
            TransitionSource.CLAIM_WORKER
            if event_kind is EventKind.APPROVAL_CONSUMED
            else TransitionSource.RECONCILIATION_WORKER
            if event_kind in {EventKind.RECONCILIATION_RECORDED, EventKind.LATE_RESULT_IGNORED}
            else TransitionSource.DISPATCH_WORKER
        )
        actor = CLAIM
    draft = ActionEventDraft(
        action_id=ACTION_ID,
        operation_id=OPERATION_ID,
        event_kind=event_kind,
        source=source,
        actor=actor,
        occurred_at=NOW,
        details=details,
    )

    with sessions.begin() as session:
        record = append_action_event(session, draft, expected_action_revision=0)

    with sessions() as session:
        assert read_action_event_history(session, ACTION_ID) == (record,)
        assert record.actor == actor
        assert record.details == details


def test_event_draft_rejects_untyped_or_wrong_kind_details() -> None:
    with pytest.raises(InvalidEventDetails) as untyped:
        ActionEventDraft(
            action_id=ACTION_ID,
            operation_id=OPERATION_ID,
            event_kind=EventKind.ACTION_PREPARED,
            source=TransitionSource.TRUSTED_SYSTEM,
            actor=ACTOR,
            occurred_at=NOW,
            details=cast(ActionEventDetails, {"reason": "arbitrary text"}),
        )
    assert untyped.value.code is Phase6ErrorCode.INVALID_EVENT_DETAILS

    with pytest.raises(InvalidEventDetails):
        ActionEventDraft(
            action_id=ACTION_ID,
            operation_id=OPERATION_ID,
            event_kind=EventKind.ACTION_PREPARED,
            source=TransitionSource.TRUSTED_SYSTEM,
            actor=ACTOR,
            occurred_at=NOW,
            details=InvalidatedDetails(reason=TrustedInvalidationReason.BINDING_DRIFT),
        )


class CountingSession(Session):
    commits = 0
    rollbacks = 0

    def commit(self) -> None:
        CountingSession.commits += 1
        super().commit()

    def rollback(self) -> None:
        CountingSession.rollbacks += 1
        super().rollback()


def test_repository_transaction_commits_or_rolls_back_exactly_once(engine: Engine) -> None:
    CountingSession.commits = 0
    CountingSession.rollbacks = 0
    factory = sessionmaker(
        bind=engine,
        class_=CountingSession,
        autoflush=False,
        expire_on_commit=False,
    )
    repository = ApprovalRepository(cast(sessionmaker[Session], factory))

    with repository.transaction():
        pass
    assert (CountingSession.commits, CountingSession.rollbacks) == (1, 0)

    with pytest.raises(RuntimeError, match="forced"):
        with repository.transaction():
            raise RuntimeError("forced")
    assert (CountingSession.commits, CountingSession.rollbacks) == (1, 1)


def test_event_history_query_is_ordered_by_sequence(sessions: sessionmaker[Session]) -> None:
    seed_action(sessions)
    newer = ActionEventORM(
        event_id="event-1",
        action_id=ACTION_ID.value,
        sequence=1,
        event_kind=EventKind.APPROVAL_REQUESTED.value,
        detail_payload=serialize_event_payload(approval_requested_event()),
        occurred_at=NOW + timedelta(minutes=1),
        actor_identity=ACTOR.value,
    )
    older = ActionEventORM(
        event_id="event-0",
        action_id=ACTION_ID.value,
        sequence=0,
        event_kind=EventKind.ACTION_PREPARED.value,
        detail_payload=serialize_event_payload(prepared_event()),
        occurred_at=NOW,
        actor_identity=ACTOR.value,
    )
    with sessions.begin() as session:
        session.add_all([newer, older])

    with sessions() as session:
        history = read_action_event_history(session, ACTION_ID)
        assert [record.sequence for record in history] == [0, 1]
        assert session.scalars(
            select(ActionEventORM).where(ActionEventORM.action_id == ACTION_ID.value)
        ).all()
