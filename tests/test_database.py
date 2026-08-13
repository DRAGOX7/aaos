"""Foundation tests for SQLite persistence and Alembic migrations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pytest import MonkeyPatch
from sqlalchemy import inspect, text

from aaos.persistence.database import (
    build_sqlite_url,
    create_database_engine,
    create_session_factory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"


def assert_import_does_not_create_database(module_name: str, tmp_path: Path) -> None:
    """Import a module in a fresh process and assert that it performs no DB writes."""
    script = f"import sys; sys.path.insert(0, {str(SOURCE_ROOT)!r}); import {module_name}"

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "data" / "database" / "aaos.db").exists()


def make_alembic_config(database_path: Path) -> Config:
    """Build an Alembic configuration for a disposable SQLite database."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def current_revision(database_path: Path) -> str | None:
    """Read the current Alembic revision directly from SQLite."""
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_database_module_import_does_not_create_database(tmp_path: Path) -> None:
    """Catch persistence imports that mutate the default database location."""
    assert_import_does_not_create_database("aaos.persistence.database", tmp_path)


def test_fastapi_import_does_not_create_database(tmp_path: Path) -> None:
    """Catch API imports that implicitly initialize persistence."""
    assert_import_does_not_create_database("aaos.main", tmp_path)


def test_default_database_url_targets_ignored_local_data(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Catch a default database path outside data/database."""
    monkeypatch.chdir(tmp_path)

    url = build_sqlite_url()

    assert url.drivername == "sqlite+pysqlite"
    assert url.database is not None
    assert Path(url.database) == tmp_path / "data" / "database" / "aaos.db"
    assert not Path(url.database).exists()


def test_explicit_engine_connects_and_executes_select(tmp_path: Path) -> None:
    """Catch broken path preparation or SQLite engine construction."""
    database_path = tmp_path / "nested" / "aaos.db"

    engine = create_database_engine(database_path)
    try:
        assert database_path.parent.is_dir()
        assert not database_path.exists()

        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1

        assert database_path.is_file()
    finally:
        engine.dispose()


def test_session_factory_opens_working_sessions(tmp_path: Path) -> None:
    """Catch a session factory that is unbound or opens sessions globally."""
    engine = create_database_engine(tmp_path / "aaos.db")
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()


def test_alembic_baseline_round_trip(tmp_path: Path) -> None:
    """Catch baseline and Phase 1 migrations that cannot round-trip cleanly."""
    database_path = tmp_path / "migration" / "aaos.db"
    config = make_alembic_config(database_path)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    assert head_revision is not None

    command.upgrade(config, "0001_baseline")
    assert current_revision(database_path) == "0001_baseline"

    engine = create_database_engine(database_path)
    try:
        assert inspect(engine).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    assert current_revision(database_path) == head_revision

    engine = create_database_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "courses",
            "assignments",
            "announcements",
            "remote_files",
        }
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    assert current_revision(database_path) is None

    command.upgrade(config, "head")
    assert current_revision(database_path) == head_revision
