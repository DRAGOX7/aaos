from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from aaos.rag.database import (
    RagDatabaseConfigurationError,
    create_existing_rag_database_engine,
    validate_rag_database,
)


def _upgrade_database(database_path: Path, revision: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(config, revision)


def test_phase4_revision_is_accepted(tmp_path: Path) -> None:
    database_path = tmp_path / "aaos.db"
    _upgrade_database(database_path, "0004_phase4_local_rag")

    validate_rag_database(database_path)


def test_current_phase8_revision_is_accepted(tmp_path: Path) -> None:
    database_path = tmp_path / "aaos.db"
    _upgrade_database(database_path, "0007_phase8_visual_provenance")

    validate_rag_database(database_path)


def test_pre_phase4_revision_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "aaos.db"
    _upgrade_database(database_path, "0003_phase3_document_ingestion")

    with pytest.raises(RagDatabaseConfigurationError):
        validate_rag_database(database_path)


def test_unknown_revision_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "aaos.db"

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "INSERT INTO alembic_version(version_num) VALUES ('not-a-real-aaos-revision')"
            )
    finally:
        engine.dispose()

    with pytest.raises(RagDatabaseConfigurationError):
        validate_rag_database(database_path)


def test_missing_database_is_rejected_without_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "missing.db"

    with pytest.raises(RagDatabaseConfigurationError):
        create_existing_rag_database_engine(database_path)

    assert not database_path.exists()


def test_database_disappearing_after_validation_is_not_recreated(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "aaos.db"
    _upgrade_database(database_path, "0007_phase8_visual_provenance")

    validate_rag_database(database_path)
    database_path.unlink()

    with pytest.raises(RagDatabaseConfigurationError):
        create_existing_rag_database_engine(database_path)

    assert not database_path.exists()


def test_existing_only_engine_enables_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "aaos.db"
    _upgrade_database(database_path, "0007_phase8_visual_provenance")

    engine = create_existing_rag_database_engine(database_path)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()
