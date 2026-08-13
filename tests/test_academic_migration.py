from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def revision(database_path: Path) -> str | None:
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_phase1_upgrade_downgrade_has_exact_domain_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase1.db"
    config = make_config(database_path)
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None

    command.upgrade(config, "head")
    assert revision(database_path) == head
    engine = create_database_engine(database_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables == {
            "alembic_version",
            "courses",
            "assignments",
            "announcements",
            "remote_files",
        }
    finally:
        engine.dispose()

    command.downgrade(config, "0001_baseline")
    engine = create_database_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
