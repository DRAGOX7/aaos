"""Narrow Moodle-to-Phase-1 synchronization integration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.normalization import normalize_collection
from aaos.academic.source import SourceCollection, SourceCollectionFailure
from aaos.academic.sync import AcademicSyncService
from aaos.persistence.database import create_database_engine, create_session_factory


class MoodleSyncPreconditionError(RuntimeError):
    """Raised when Moodle collection did not produce a complete snapshot."""


def sync_moodle_snapshot(
    collection: SourceCollection,
    *,
    session_factory: Callable[[], Session],
) -> None:
    """Normalize one complete collection and synchronize it through Phase 1."""
    if isinstance(collection, SourceCollectionFailure):
        raise MoodleSyncPreconditionError("Moodle source collection is incomplete")

    snapshot = normalize_collection(collection)
    AcademicSyncService(session_factory).sync(snapshot)


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
