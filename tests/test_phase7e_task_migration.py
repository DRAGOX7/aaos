from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def config_for(path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(path))
    return config


def revision(path: Path) -> str | None:
    engine = create_database_engine(path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_task_records_migration_is_additive_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "task.db"
    config = config_for(path)
    command.upgrade(config, "0005_phase6a_approval_core")
    # This test owns the historical Phase 7E migration, not the moving repository head.
    command.upgrade(config, "0006_phase7e_task_records")

    assert revision(path) == "0006_phase7e_task_records"
    engine = create_database_engine(path)
    try:
        assert "task_records" in inspect(engine).get_table_names()
        assert {column["name"] for column in inspect(engine).get_columns("task_records")} == {
            "task_id",
            "task_kind",
            "actor_identity",
            "idempotency_key",
            "request_fingerprint",
            "creation_state",
            "failure_code",
            "action_id",
            "created_at",
            "updated_at",
        }
    finally:
        engine.dispose()

    command.downgrade(config, "0005_phase6a_approval_core")
    assert revision(path) == "0005_phase6a_approval_core"
    engine = create_database_engine(path)
    try:
        assert "task_records" not in inspect(engine).get_table_names()
        assert "prepared_actions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_task_records_enforce_closed_values_and_identities(tmp_path: Path) -> None:
    path = tmp_path / "task.db"
    config = config_for(path)
    command.upgrade(config, "head")
    engine = create_database_engine(path)
    try:
        values = {
            "task_id": "task-1",
            "task_kind": "CALENDAR_CREATE_EVENT",
            "actor_identity": "actor-1",
            "idempotency_key": "request-1",
            "request_fingerprint": "a" * 64,
            "creation_state": "CREATED",
            "failure_code": None,
            "action_id": None,
            "created_at": "2026-09-18T10:00:00Z",
            "updated_at": "2026-09-18T10:00:00Z",
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO task_records "
                    "(task_id, task_kind, actor_identity, idempotency_key, "
                    "request_fingerprint, creation_state, failure_code, action_id, "
                    "created_at, updated_at) VALUES "
                    "(:task_id, :task_kind, :actor_identity, :idempotency_key, "
                    ":request_fingerprint, :creation_state, :failure_code, :action_id, "
                    ":created_at, :updated_at)"
                ),
                values,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO task_records SELECT :id, task_kind, actor_identity, idempotency_key, request_fingerprint, 'CREATED', NULL, NULL, created_at, updated_at FROM task_records WHERE task_id=:old"
                    ),
                    {"id": "task-2", "old": "task-1"},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE task_records SET task_kind='ARBITRARY' WHERE task_id='task-1'")
                )
    finally:
        engine.dispose()
