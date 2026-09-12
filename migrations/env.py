"""Alembic environment for the AAOS SQLite migration baseline."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection

from aaos.persistence import (
    approval_models,  # noqa: F401
    document_models,  # noqa: F401
    rag_models,  # noqa: F401
)
from aaos.persistence.academic_models import Base
from aaos.persistence.database import (
    DEFAULT_DATABASE_PATH,
    build_sqlite_url,
    create_database_engine,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def configured_database_path() -> Path:
    """Return the configured path or the ignored local-development default."""
    configured_path = config.get_main_option("aaos.database_path")
    return Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""
    database_url = build_sqlite_url(configured_database_path())
    context.configure(
        url=str(database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations(connection: Connection) -> None:
    """Run migrations through an explicit SQLAlchemy connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured local SQLite database."""
    engine = create_database_engine(configured_database_path())
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
