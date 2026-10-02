"""Explicit, fail-closed CLI composition for live Moodle synchronization."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.academic.source import (
    CompleteSourceSnapshot,
    SourceCollection,
    SourceCollectionFailure,
)
from aaos.moodle.browser import (
    BrowserSmokeFailure,
    _run_same_context_complete_collection,
)
from aaos.moodle.integration import MoodleSyncPreconditionError, sync_moodle_snapshot
from aaos.moodle.receiver import (
    ReceiverError,
    RuntimeMoodleEnvironment,
    load_runtime_moodle_environment,
)
from aaos.moodle.sync_history import (
    MoodleSyncFailureCode,
    MoodleSyncHistoryRepository,
)
from aaos.persistence.database import (
    DEFAULT_DATABASE_PATH,
    create_session_factory,
    resolve_database_path,
)

_LOGGER = logging.getLogger(__name__)


def _database_is_present(database_path: Path) -> None:
    try:
        if not database_path.is_file() or database_path.resolve(strict=True) != database_path:
            raise ValueError("database unavailable")
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("database unavailable") from None


def _normal_database_path() -> Path:
    """Resolve the canonical academic application database path only."""
    return resolve_database_path(DEFAULT_DATABASE_PATH)


def _create_existing_database_engine(database_path: Path) -> Engine:
    """Open an existing SQLite file without allowing SQLite to create one."""
    resolved_path = database_path.resolve(strict=True)
    if not resolved_path.is_file():
        raise ValueError("database unavailable")

    database_uri = f"{resolved_path.as_uri()}?mode=rw"

    def connect_existing() -> sqlite3.Connection:
        return sqlite3.connect(database_uri, uri=True)

    engine = sqlalchemy_create_engine("sqlite+pysqlite://", creator=connect_existing)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def collect_configured_snapshot(environment: RuntimeMoodleEnvironment) -> SourceCollection:
    """Use the configured authenticated browser and restricted source only."""
    from aaos.moodle.browser_runtime import launch_msedge_persistent_context

    return _run_same_context_complete_collection(
        settings=replace(environment.settings, mode="browser"),
        paths=environment.auth_paths,
        launch_persistent_context=launch_msedge_persistent_context,
        canonical_wwwroot=environment.bootstrap_config.canonical_wwwroot,
    )


def _persist_snapshot(
    collection: CompleteSourceSnapshot,
    *,
    session_factory: Callable[[], Session],
    sync_attempt_id: int,
) -> None:
    sync_moodle_snapshot(
        collection,
        session_factory=session_factory,
        sync_attempt_id=sync_attempt_id,
    )


def _render_failure(stage: str) -> int:
    print("MOODLE_SYNC_FAILED")
    print(f"FAILURE_STAGE={stage}")
    return 1


def run_configured_moodle_sync() -> int:
    """Run one complete browser collection, then one normal-DB synchronization."""
    return run_configured_moodle_sync_outcome().exit_code


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """The CLI exit code plus, for browser failures, the fixed non-sensitive stage."""

    exit_code: int
    browser_stage: str | None = None


def run_configured_moodle_sync_outcome() -> SyncOutcome:
    """The same sync as ``run_configured_moodle_sync``, reporting why it failed."""
    try:
        environment = load_runtime_moodle_environment()
        database_path = _normal_database_path()
    except (ReceiverError, OSError, TypeError, ValueError):
        return SyncOutcome(_render_failure("CONFIGURATION"))
    try:
        _database_is_present(database_path)
    except ValueError:
        return SyncOutcome(_render_failure("DATABASE"))

    engine: Engine | None = None
    try:
        try:
            engine = _create_existing_database_engine(database_path)
            session_factory = create_session_factory(engine)
            history = MoodleSyncHistoryRepository(session_factory)
            sync_attempt_id = history.begin(environment.settings.source_system)
        except (
            OSError,
            RuntimeError,
            SQLAlchemyError,
            TypeError,
            ValueError,
        ):
            return SyncOutcome(_render_failure("PERSISTENCE"))

        try:
            collection = collect_configured_snapshot(environment)
        except BrowserSmokeFailure as error:
            history.fail(sync_attempt_id, MoodleSyncFailureCode.COLLECTION_INCOMPLETE)
            return SyncOutcome(_render_failure("BROWSER"), browser_stage=str(error.stage))
        except (OSError, RuntimeError, TypeError, ValueError):
            history.fail(sync_attempt_id, MoodleSyncFailureCode.COLLECTION_INCOMPLETE)
            return SyncOutcome(_render_failure("COLLECTION"))
        except Exception as error:
            history.fail(sync_attempt_id, MoodleSyncFailureCode.COLLECTION_INCOMPLETE)
            _LOGGER.error(
                "Moodle collection failed with unexpected exception type %s",
                type(error).__name__,
            )
            return SyncOutcome(_render_failure("COLLECTION"))

        if isinstance(collection, SourceCollectionFailure) or not isinstance(
            collection, CompleteSourceSnapshot
        ):
            history.fail(sync_attempt_id, MoodleSyncFailureCode.COLLECTION_INCOMPLETE)
            return SyncOutcome(_render_failure("COLLECTION"))

        try:
            _database_is_present(database_path)
            _persist_snapshot(
                collection,
                session_factory=session_factory,
                sync_attempt_id=sync_attempt_id,
            )
        except (
            MoodleSyncPreconditionError,
            OSError,
            RuntimeError,
            SQLAlchemyError,
            TypeError,
            ValueError,
        ):
            return SyncOutcome(_render_failure("PERSISTENCE"))

        print("MOODLE_SYNC_PASS")
        print(f"COURSE_COUNT={len(collection.courses)}")
        print(f"ASSIGNMENT_COUNT={len(collection.assignments)}")
        print(f"ANNOUNCEMENT_COUNT={len(collection.announcements)}")
        print(f"REMOTE_FILE_COUNT={len(collection.remote_files)}")
        return SyncOutcome(0)
    finally:
        if engine is not None:
            engine.dispose()


__all__ = [
    "SyncOutcome",
    "collect_configured_snapshot",
    "run_configured_moodle_sync",
    "run_configured_moodle_sync_outcome",
]
