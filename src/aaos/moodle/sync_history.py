"""Transactional run metadata for complete Moodle synchronization snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aaos.moodle.changes import NormalizedMoodleState, serialize_state
from aaos.persistence.moodle_sync_models import MoodleSyncSnapshotORM


class MoodleSyncFailureCode(StrEnum):
    COLLECTION_INCOMPLETE = "COLLECTION_INCOMPLETE"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    SYNCHRONIZATION_FAILED = "SYNCHRONIZATION_FAILED"


class MoodleSyncHistoryError(RuntimeError):
    """History could not safely record the outcome of a Moodle sync attempt."""


class MoodleSyncAlreadyRunning(MoodleSyncHistoryError):
    """A previous sync attempt for the same Moodle source remains active."""


class MoodleSyncHistoryRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    def begin(self, source_system: str, *, started_at: datetime | None = None) -> int:
        source_system = _source_system(source_system)
        started_at = _utc(started_at or datetime.now(timezone.utc), "started_at")
        session = self._session_factory()
        try:
            with session.begin():
                row = MoodleSyncSnapshotORM(
                    source_system=source_system,
                    started_at=started_at,
                    status="RUNNING",
                    snapshot_version=1,
                )
                session.add(row)
                session.flush()
                snapshot_id = row.id
            return snapshot_id
        except IntegrityError:
            session.rollback()
            raise MoodleSyncAlreadyRunning(
                "a Moodle synchronization for this source is already running"
            ) from None
        finally:
            session.close()

    def fail(
        self,
        snapshot_id: int,
        failure_code: MoodleSyncFailureCode,
        *,
        finished_at: datetime | None = None,
    ) -> None:
        if not isinstance(failure_code, MoodleSyncFailureCode):
            raise TypeError("failure_code must use the closed MoodleSyncFailureCode enum")
        finished_at = _utc(finished_at or datetime.now(timezone.utc), "finished_at")
        session = self._session_factory()
        try:
            with session.begin():
                row = session.get(MoodleSyncSnapshotORM, _positive_id(snapshot_id))
                if row is None:
                    raise MoodleSyncHistoryError("Moodle sync attempt was not found")
                if row.status == "FAILED" and row.failure_code == failure_code.value:
                    return
                if row.status != "RUNNING" or finished_at < row.started_at:
                    raise MoodleSyncHistoryError("Moodle sync attempt cannot transition to FAILED")
                row.status = "FAILED"
                row.finished_at = finished_at
                row.failure_code = failure_code.value
                row.snapshot_state = None
        except MoodleSyncHistoryError:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def complete_in_session(
        session: Session,
        snapshot_id: int,
        state: NormalizedMoodleState,
        *,
        finished_at: datetime,
    ) -> None:
        """Complete history in the same caller-owned transaction as academic upserts."""
        snapshot_id = _positive_id(snapshot_id)
        if not isinstance(state, NormalizedMoodleState):
            raise TypeError("state must be a NormalizedMoodleState")
        finished_at = _utc(finished_at, "finished_at")
        row = session.get(MoodleSyncSnapshotORM, snapshot_id)
        if row is None or row.status != "RUNNING":
            raise MoodleSyncHistoryError("Moodle sync attempt is not available for completion")
        if row.source_system != state.source_system or finished_at < row.started_at:
            raise MoodleSyncHistoryError("Moodle sync outcome does not match its attempt")
        row.status = "COMPLETE"
        row.finished_at = finished_at
        row.snapshot_version = 1
        row.snapshot_state = serialize_state(state)
        row.failure_code = None


def _source_system(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise ValueError("source_system must be bounded non-blank text")
    result = value.strip().lower()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("source_system contains unsupported characters")
    return result


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(timezone.utc)


def _positive_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("snapshot_id must be a positive integer")
    return value


__all__ = [
    "MoodleSyncAlreadyRunning",
    "MoodleSyncFailureCode",
    "MoodleSyncHistoryError",
    "MoodleSyncHistoryRepository",
]
