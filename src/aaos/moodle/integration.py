"""Narrow Moodle-to-Phase-1 synchronization integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.normalization import NormalizationError, normalize_collection
from aaos.academic.source import SourceCollection, SourceCollectionFailure
from aaos.academic.sync import AcademicSyncService
from aaos.moodle.changes import canonical_state
from aaos.moodle.sync_history import MoodleSyncFailureCode, MoodleSyncHistoryRepository
from aaos.persistence.database import create_database_engine, create_session_factory


class MoodleSyncPreconditionError(RuntimeError):
    """Raised when Moodle collection did not produce a complete snapshot."""


def sync_moodle_snapshot(
    collection: SourceCollection,
    *,
    session_factory: Callable[[], Session],
    sync_attempt_id: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Atomically persist one complete snapshot and its deterministic history."""
    history = MoodleSyncHistoryRepository(session_factory)
    if isinstance(collection, SourceCollectionFailure):
        if sync_attempt_id is not None:
            history.fail(
                sync_attempt_id,
                MoodleSyncFailureCode.COLLECTION_INCOMPLETE,
                finished_at=finished_at,
            )
        raise MoodleSyncPreconditionError("Moodle source collection is incomplete")

    try:
        snapshot = normalize_collection(collection)
    except NormalizationError:
        if sync_attempt_id is not None:
            history.fail(
                sync_attempt_id,
                MoodleSyncFailureCode.NORMALIZATION_FAILED,
                finished_at=finished_at,
            )
        raise

    attempt_id = sync_attempt_id
    if attempt_id is None:
        attempt_id = history.begin(snapshot.source_system, started_at=started_at)
    completed_at = finished_at or datetime.now().astimezone()
    state = canonical_state(snapshot)
    session = session_factory()
    try:
        with session.begin():
            AcademicSyncService(session_factory).sync_in_session(session, snapshot)
            history.complete_in_session(
                session,
                attempt_id,
                state,
                finished_at=completed_at,
            )
    except SQLAlchemyError:
        session.rollback()
        history.fail(attempt_id, MoodleSyncFailureCode.SYNCHRONIZATION_FAILED)
        raise
    except Exception:
        session.rollback()
        history.fail(attempt_id, MoodleSyncFailureCode.SYNCHRONIZATION_FAILED)
        raise
    finally:
        session.close()


def build_isolated_database(
    path: Path,
) -> tuple[Engine, sessionmaker[Session]]:
    """Create a fresh caller-selected SQLite database using normal migrations."""
    database_path = path.expanduser().resolve()
    if database_path.exists():
        raise ValueError("isolated database path must not already exist")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[3]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(config, "head")

    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    return engine, factory
