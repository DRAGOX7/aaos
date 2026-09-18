"""Transactional repository for Phase 7E durable task records."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import ActionId, ActorIdentity
from aaos.persistence.task_models import TaskRecordORM
from aaos.tasks.contracts import TaskCreationState, TaskFailureCode, TaskKind, TaskRecord


@contextmanager
def _transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _record(row: TaskRecordORM) -> TaskRecord:
    return TaskRecord(
        task_id=row.task_id,
        task_kind=TaskKind(row.task_kind),
        actor_identity=ActorIdentity(row.actor_identity),
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        creation_state=TaskCreationState(row.creation_state),
        failure_code=TaskFailureCode(row.failure_code) if row.failure_code else None,
        action_id=ActionId(row.action_id) if row.action_id else None,
        created_at=row.created_at,  # type: ignore[arg-type]
        updated_at=row.updated_at,  # type: ignore[arg-type]
    )


class TaskRepository:
    """Own task sessions and expose only bounded CAS operations."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        if not isinstance(session_factory, sessionmaker):
            raise TypeError("session_factory must be a SQLAlchemy sessionmaker")
        self._session_factory = session_factory

    def create_or_get(self, record: TaskRecord) -> tuple[TaskRecord, bool]:
        if not isinstance(record, TaskRecord):
            raise TypeError("record must be a TaskRecord")
        with _transaction(self._session_factory) as session:
            existing = session.scalar(
                select(TaskRecordORM).where(
                    TaskRecordORM.actor_identity == record.actor_identity.value,
                    TaskRecordORM.idempotency_key == record.idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != record.request_fingerprint:
                    raise ValueError("idempotency key conflicts with request fingerprint")
                return _record(existing), False
            session.add(
                TaskRecordORM(
                    task_id=record.task_id,
                    task_kind=record.task_kind.value,
                    actor_identity=record.actor_identity.value,
                    idempotency_key=record.idempotency_key,
                    request_fingerprint=record.request_fingerprint,
                    creation_state=record.creation_state.value,
                    failure_code=record.failure_code.value if record.failure_code else None,
                    action_id=record.action_id.value if record.action_id else None,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(TaskRecordORM).where(
                        TaskRecordORM.actor_identity == record.actor_identity.value,
                        TaskRecordORM.idempotency_key == record.idempotency_key,
                    )
                )
                if existing is None:
                    raise
                if existing.request_fingerprint != record.request_fingerprint:
                    raise ValueError("idempotency key conflicts with request fingerprint")
                return _record(existing), False
            return record, True

    def get(self, task_id: str) -> TaskRecord | None:
        with self._session_factory() as session:
            row = session.get(TaskRecordORM, task_id)
            return _record(row) if row is not None else None

    def get_by_idempotency(
        self, actor_identity: ActorIdentity, idempotency_key: str
    ) -> TaskRecord | None:
        if not isinstance(actor_identity, ActorIdentity):
            raise TypeError("actor_identity must be an ActorIdentity")
        with self._session_factory() as session:
            row = session.scalar(
                select(TaskRecordORM).where(
                    TaskRecordORM.actor_identity == actor_identity.value,
                    TaskRecordORM.idempotency_key == idempotency_key,
                )
            )
            return _record(row) if row is not None else None

    def cas_creation_state(
        self,
        task_id: str,
        *,
        expected: TaskCreationState,
        target: TaskCreationState,
        action_id: ActionId | None,
        failure_code: str | None,
        now: datetime,
    ) -> TaskRecord:
        if not isinstance(expected, TaskCreationState) or not isinstance(target, TaskCreationState):
            raise TypeError("states must be TaskCreationState values")
        if expected is not TaskCreationState.CREATED or target not in {
            TaskCreationState.ACTION_BOUND,
            TaskCreationState.FAILED,
        }:
            raise ValueError("unsupported task creation state transition")
        if action_id is not None and not isinstance(action_id, ActionId):
            raise TypeError("action_id must be an ActionId")
        if target is TaskCreationState.FAILED:
            if failure_code not in {member.value for member in TaskFailureCode}:
                raise ValueError("failure_code must be a closed TaskFailureCode")
            if action_id is not None:
                raise ValueError("failed task cannot link an action")
        elif failure_code is not None or action_id is None:
            raise ValueError("action-bound task requires an action and no failure")
        with _transaction(self._session_factory) as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                update(TaskRecordORM)
                .where(
                    TaskRecordORM.task_id == task_id,
                    TaskRecordORM.creation_state == expected.value,
                )
                .values(
                    creation_state=target.value,
                    action_id=action_id.value if action_id else None,
                    failure_code=failure_code,
                    updated_at=now,
                ),
            )
            )
            if result.rowcount != 1:
                raise ValueError("task creation state CAS failed")
            row = session.get(TaskRecordORM, task_id)
            if row is None:
                raise ValueError("task not found")
            return _record(row)

    def list(self, *, limit: int) -> tuple[TaskRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._session_factory() as session:
            rows = session.scalars(
                select(TaskRecordORM)
                .order_by(TaskRecordORM.created_at.asc(), TaskRecordORM.task_id.asc())
                .limit(limit)
            ).all()
            return tuple(_record(row) for row in rows)


__all__ = ["TaskRepository"]
