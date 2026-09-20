"""Fail-closed SQLite composition for configured local-RAG runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from sqlalchemy import URL, Engine, event
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.exc import SQLAlchemyError

_REQUIRED_REVISION = "0004_phase4_local_rag"


class RagDatabaseConfigurationError(RuntimeError):
    """Fixed, safe failure for configured RAG database composition."""


def _configuration_failure() -> RagDatabaseConfigurationError:
    return RagDatabaseConfigurationError("rag database configuration failed")


def validate_rag_database(database_path: Path) -> None:
    """Require an existing database whose migration descends from Phase 4."""
    engine: Engine | None = None

    try:
        if not database_path.is_file():
            raise _configuration_failure()

        if database_path.resolve(strict=True) != database_path:
            raise _configuration_failure()

        engine = create_existing_rag_database_engine(database_path)

        try:
            with engine.connect() as connection:
                revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
        finally:
            engine.dispose()
            engine = None

    except RagDatabaseConfigurationError:
        raise
    except (OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
        raise _configuration_failure() from None
    finally:
        if engine is not None:
            engine.dispose()

    if not isinstance(revision, str):
        raise _configuration_failure()

    if not _is_compatible_migration_revision(revision):
        raise _configuration_failure()


def _is_compatible_migration_revision(revision: str) -> bool:
    """Return whether revision is Phase 4 or a repository descendant."""
    try:
        config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
        script = ScriptDirectory.from_config(config)

        tuple(
            script.revision_map.iterate_revisions(
                revision,
                _REQUIRED_REVISION,
                inclusive=True,
            )
        )
    except (RevisionError, OSError, RuntimeError, TypeError, ValueError):
        return False

    return True


def create_existing_rag_database_engine(database_path: Path) -> Engine:
    """Open an existing SQLite database without permitting creation."""
    engine: Engine | None = None

    try:
        resolved_path = database_path.resolve(strict=True)

        if not resolved_path.is_file() or resolved_path != database_path:
            raise _configuration_failure()

        url = URL.create(
            drivername="sqlite+pysqlite",
            database=f"file:{resolved_path.as_posix()}",
            query={"mode": "rw", "uri": "true"},
        )

        engine = sqlalchemy_create_engine(url)

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            _connection_record: Any,
        ) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        # Force the existing-only URI to open now rather than leaving
        # SQLAlchemy's Engine lazy.
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

        return engine

    except RagDatabaseConfigurationError:
        if engine is not None:
            engine.dispose()
        raise
    except (OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
        if engine is not None:
            engine.dispose()
        raise _configuration_failure() from None


__all__ = [
    "RagDatabaseConfigurationError",
    "create_existing_rag_database_engine",
    "validate_rag_database",
]
