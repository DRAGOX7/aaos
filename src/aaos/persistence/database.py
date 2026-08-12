"""Explicit SQLAlchemy construction helpers for local SQLite persistence."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import URL, Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_PATH = Path("data/database/aaos.db")


def resolve_database_path(database_path: str | Path = DEFAULT_DATABASE_PATH) -> Path:
    """Resolve a caller-provided database path without creating it."""
    return Path(database_path).expanduser().resolve()


def build_sqlite_url(database_path: str | Path = DEFAULT_DATABASE_PATH) -> URL:
    """Build an explicit SQLAlchemy URL for a local SQLite database."""
    resolved_path = resolve_database_path(database_path)
    return URL.create(drivername="sqlite+pysqlite", database=str(resolved_path))


def create_database_engine(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    echo: bool = False,
) -> Engine:
    """Create an engine after deliberately preparing its parent directory."""
    resolved_path = resolve_database_path(database_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlalchemy_create_engine(build_sqlite_url(resolved_path), echo=echo)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory without opening a global live session."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
