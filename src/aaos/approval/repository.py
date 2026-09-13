"""Transactional persistence primitives for the Phase 6A approval ledger."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, ContextManager, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import (
    ActionId,
    ActionState,
    EventId,
    EventKind,
    ExecutionOutcome,
    OperationId,
    OperationState,
    Phase6ErrorCode,
    TrustedApproverContext,
)
from aaos.approval.events import (
    ActionEventDraft,
    ActionEventRecord,
    deserialize_event_payload,
    serialize_event_payload,
)
from aaos.approval.state import (
    TransitionSource,
    validate_action_transition,
    validate_operation_transition,
)
from aaos.persistence.approval_models import (
    ActionEventCounterORM,
    ActionEventORM,
    ActionLifecycleORM,
    OperationRecordORM,
)

_TERMINAL_ACTION_STATES = frozenset(
    {
        ActionState.CONFIRMED_SUCCESS,
        ActionState.CONFIRMED_FAILURE,
        ActionState.REJECTED,
        ActionState.INVALIDATED,
        ActionState.EXPIRED,
        ActionState.REVOKED,
    }
)


class PersistenceConflict(RuntimeError):
    """A stable persistence conflict that does not expose database details."""

    def __init__(self) -> None:
        self.code = Phase6ErrorCode.PERSISTENCE_CONFLICT
        super().__init__(self.code.value)


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("revision must be a non-negative integer")
    return value


def allocate_event_sequence(session: Session, action_id: ActionId) -> int:
    """Atomically reserve the next per-action sequence in the caller's transaction."""

    if not isinstance(session, Session) or not isinstance(action_id, ActionId):
        raise TypeError("session and action_id must be trusted repository values")
    try:
        session.execute(
            sqlite_insert(ActionEventCounterORM)
            .values(action_id=action_id.value, next_sequence=0)
            .on_conflict_do_nothing(index_elements=[ActionEventCounterORM.action_id])
        )
        sequence = session.execute(
            update(ActionEventCounterORM)
            .where(ActionEventCounterORM.action_id == action_id.value)
            .values(next_sequence=ActionEventCounterORM.next_sequence + 1)
            .returning(ActionEventCounterORM.next_sequence - 1)
        ).scalar_one()
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    return int(sequence)


def _actor_identity(draft: ActionEventDraft) -> str:
    if isinstance(draft.actor, TrustedApproverContext):
        return draft.actor.approver_identity.value
    return draft.actor.value


def _insert_action_event(session: Session, draft: ActionEventDraft) -> ActionEventRecord:
    sequence = allocate_event_sequence(session, draft.action_id)
    record = ActionEventRecord(
        event_id=EventId(uuid4().hex),
        action_id=draft.action_id,
        operation_id=draft.operation_id,
        sequence=sequence,
        event_kind=draft.event_kind,
        source=draft.source,
        actor=draft.actor,
        occurred_at=draft.occurred_at,
        details=draft.details,
    )
    session.add(
        ActionEventORM(
            event_id=record.event_id.value,
            action_id=record.action_id.value,
            sequence=record.sequence,
            event_kind=record.event_kind.value,
            detail_payload=serialize_event_payload(draft),
            occurred_at=record.occurred_at,
            actor_identity=_actor_identity(draft),
        )
    )
    try:
        session.flush()
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    return record


def append_action_event(
    session: Session,
    draft: ActionEventDraft,
    *,
    expected_action_revision: int | None,
) -> ActionEventRecord:
    """Append an event, optionally consuming one lifecycle revision through CAS."""

    if not isinstance(session, Session) or not isinstance(draft, ActionEventDraft):
        raise TypeError("session and draft must be trusted repository values")
    if expected_action_revision is not None:
        revision = _revision(expected_action_revision)
        try:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(ActionLifecycleORM)
                    .where(
                        ActionLifecycleORM.action_id == draft.action_id.value,
                        ActionLifecycleORM.revision == revision,
                    )
                    .values(revision=revision + 1, updated_at=draft.occurred_at)
                ),
            )
        except SQLAlchemyError:
            raise PersistenceConflict() from None
        if result.rowcount != 1:
            raise PersistenceConflict()
    return _insert_action_event(session, draft)


def cas_action_lifecycle(
    session: Session,
    *,
    action_id: ActionId,
    expected_state: ActionState,
    expected_revision: int,
    new_state: ActionState,
    event: ActionEventDraft,
) -> bool:
    """Apply a lifecycle transition and append its event as one caller-owned unit."""

    if (
        not isinstance(session, Session)
        or not isinstance(action_id, ActionId)
        or not isinstance(expected_state, ActionState)
        or not isinstance(new_state, ActionState)
        or not isinstance(event, ActionEventDraft)
    ):
        raise TypeError("invalid action CAS values")
    if event.action_id != action_id:
        raise ValueError("event action_id does not match CAS action_id")
    revision = _revision(expected_revision)
    validate_action_transition(expected_state, new_state, event.source)
    terminal_at = event.occurred_at if new_state in _TERMINAL_ACTION_STATES else None
    try:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ActionLifecycleORM)
                .where(
                    ActionLifecycleORM.action_id == action_id.value,
                    ActionLifecycleORM.state == expected_state.value,
                    ActionLifecycleORM.revision == revision,
                )
                .values(
                    state=new_state.value,
                    revision=revision + 1,
                    updated_at=event.occurred_at,
                    terminal_at=terminal_at,
                )
            ),
        )
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if result.rowcount != 1:
        return False
    _insert_action_event(session, event)
    return True


def cas_operation(
    session: Session,
    *,
    operation_id: OperationId,
    expected_state: OperationState,
    expected_revision: int,
    new_state: OperationState,
) -> bool:
    """Apply a revision-checked operation-state transition."""

    if (
        not isinstance(session, Session)
        or not isinstance(operation_id, OperationId)
        or not isinstance(expected_state, OperationState)
        or not isinstance(new_state, OperationState)
    ):
        raise TypeError("invalid operation CAS values")
    revision = _revision(expected_revision)
    validate_operation_transition(expected_state, new_state, _operation_source(expected_state))
    try:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(OperationRecordORM)
                .where(
                    OperationRecordORM.operation_id == operation_id.value,
                    OperationRecordORM.state == expected_state.value,
                    OperationRecordORM.revision == revision,
                )
                .values(state=new_state.value, revision=revision + 1)
            ),
        )
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    return result.rowcount == 1


def _operation_source(expected_state: OperationState) -> TransitionSource:
    """Resolve the unique worker source encoded by the frozen operation graph."""

    if expected_state in {OperationState.CLAIMED, OperationState.EXECUTING}:
        return TransitionSource.DISPATCH_WORKER
    if expected_state in {OperationState.UNKNOWN_OUTCOME, OperationState.RECONCILIATION_PENDING}:
        return TransitionSource.RECONCILIATION_WORKER
    raise ValueError("expected_state has no outgoing operation transitions")


def read_action_event_history(session: Session, action_id: ActionId) -> Sequence[ActionEventRecord]:
    """Read immutable action events in authoritative per-action sequence order."""

    if not isinstance(session, Session) or not isinstance(action_id, ActionId):
        raise TypeError("session and action_id must be trusted repository values")
    try:
        rows = session.scalars(
            select(ActionEventORM)
            .where(ActionEventORM.action_id == action_id.value)
            .order_by(ActionEventORM.sequence)
        ).all()
        records: list[ActionEventRecord] = []
        for row in rows:
            event_kind = EventKind(row.event_kind)
            operation_id, source, actor, details = deserialize_event_payload(
                row.detail_payload, event_kind=event_kind
            )
            stored_identity = (
                actor.approver_identity.value
                if isinstance(actor, TrustedApproverContext)
                else actor.value
            )
            if stored_identity != row.actor_identity:
                raise PersistenceConflict()
            records.append(
                ActionEventRecord(
                    event_id=EventId(row.event_id),
                    action_id=ActionId(row.action_id),
                    operation_id=operation_id,
                    sequence=row.sequence,
                    event_kind=event_kind,
                    source=source,
                    actor=actor,
                    occurred_at=row.occurred_at,
                    details=details,
                )
            )
    except PersistenceConflict:
        raise
    except (SQLAlchemyError, TypeError, ValueError):
        raise PersistenceConflict() from None
    return tuple(records)


def _state_row(session: Session, operation_id: OperationId) -> tuple[str, str, str | None]:
    if not isinstance(session, Session) or not isinstance(operation_id, OperationId):
        raise TypeError("session and operation_id must be trusted repository values")
    try:
        row = session.execute(
            select(
                ActionLifecycleORM.state,
                OperationRecordORM.state,
                OperationRecordORM.outcome_kind,
            )
            .join(
                OperationRecordORM,
                OperationRecordORM.action_id == ActionLifecycleORM.action_id,
            )
            .where(OperationRecordORM.operation_id == operation_id.value)
        ).one_or_none()
    except SQLAlchemyError:
        raise PersistenceConflict() from None
    if row is None:
        raise PersistenceConflict()
    return row[0], row[1], row[2]


def read_states(session: Session, operation_id: OperationId) -> tuple[ActionState, OperationState]:
    action_state, operation_state, _ = _state_row(session, operation_id)
    try:
        return ActionState(action_state), OperationState(operation_state)
    except ValueError:
        raise PersistenceConflict() from None


def read_states_and_outcome(
    session: Session,
    operation_id: OperationId,
) -> tuple[ActionState, OperationState, ExecutionOutcome | None]:
    action_state, operation_state, outcome = _state_row(session, operation_id)
    try:
        return (
            ActionState(action_state),
            OperationState(operation_state),
            None if outcome is None else ExecutionOutcome(outcome),
        )
    except ValueError:
        raise PersistenceConflict() from None


@contextmanager
def _transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    else:
        try:
            session.commit()
        except BaseException:
            session.rollback()
            raise
    finally:
        session.close()


class ApprovalRepository:
    """Own sessions while callers own all writes inside one explicit transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        if not isinstance(session_factory, sessionmaker):
            raise TypeError("session_factory must be a SQLAlchemy sessionmaker")
        self._session_factory = session_factory

    def transaction(self) -> ContextManager[Session]:
        return _transaction(self._session_factory)


__all__ = [
    "ApprovalRepository",
    "PersistenceConflict",
    "allocate_event_sequence",
    "append_action_event",
    "cas_action_lifecycle",
    "cas_operation",
    "read_action_event_history",
    "read_states",
    "read_states_and_outcome",
]
