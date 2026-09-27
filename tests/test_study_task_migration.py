from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "0009_rag_structural_provenance"
STUDY_TASK_REVISION = "0010_study_tasks"


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def _revision(database_path: Path) -> str | None:
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_study_task_migration_preserves_existing_data_and_downgrades(tmp_path: Path) -> None:
    path = tmp_path / "study-tasks.db"
    config = _config(path)
    command.upgrade(config, BASE_REVISION)
    engine = create_database_engine(path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO courses (id, source_system, source_id, title, is_active) "
                    "VALUES (41, 'test', 'course-41', 'Existing course', 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assignments "
                    "(id, course_id, source_system, source_id, title, due_at, is_active) "
                    "VALUES (73, 41, 'test', 'assignment-73', 'Existing assignment', "
                    "'2026-10-01T12:00:00Z', 1)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    assert _revision(path) == STUDY_TASK_REVISION
    engine = create_database_engine(path)
    try:
        inspector = inspect(engine)
        assert "study_tasks" in inspector.get_table_names()
        assert "study_task_mutations" in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns("study_tasks")} == {
            "id",
            "title",
            "status",
            "priority",
            "course_id",
            "assignment_id",
            "created_at",
            "updated_at",
        }
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT title FROM courses WHERE id=41")).scalar_one()
                == "Existing course"
            )
            assert (
                connection.execute(text("SELECT title FROM assignments WHERE id=73")).scalar_one()
                == "Existing assignment"
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO study_tasks "
                        "(title, status, priority, created_at, updated_at) "
                        "VALUES ('Invalid', 'IN_PROGRESS', 'URGENT', "
                        "'2026-09-27T12:00:00Z', '2026-09-27T12:00:00Z')"
                    )
                )
    finally:
        engine.dispose()

    command.downgrade(config, BASE_REVISION)
    assert _revision(path) == BASE_REVISION
    engine = create_database_engine(path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "study_tasks" not in tables
        assert "study_task_mutations" not in tables
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT title FROM courses WHERE id=41")).scalar_one()
                == "Existing course"
            )
            assert (
                connection.execute(text("SELECT title FROM assignments WHERE id=73")).scalar_one()
                == "Existing assignment"
            )
    finally:
        engine.dispose()
