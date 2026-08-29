"""Migration tests for the Phase 3 document-ingestion schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import LargeBinary, inspect

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE1_TABLES = {
    "alembic_version",
    "courses",
    "assignments",
    "announcements",
    "remote_files",
}
DOCUMENT_TABLES = {
    "documents",
    "content_blobs",
    "document_versions",
    "document_pages",
}


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


def test_phase3_upgrade_adds_exact_document_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    command.upgrade(make_config(database_path), "0003_phase3_document_ingestion")

    assert revision(database_path) == "0003_phase3_document_ingestion"
    engine = create_database_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == PHASE1_TABLES | DOCUMENT_TABLES
    finally:
        engine.dispose()


def test_phase3_downgrade_preserves_phase1_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    config = make_config(database_path)
    command.upgrade(config, "0003_phase3_document_ingestion")

    command.downgrade(config, "0002_phase1_academic_domain")

    assert revision(database_path) == "0002_phase1_academic_domain"
    engine = create_database_engine(database_path)
    try:
        assert set(inspect(engine).get_table_names()) == PHASE1_TABLES
    finally:
        engine.dispose()


def test_phase3_schema_exposes_required_named_indexes(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    command.upgrade(make_config(database_path), "0003_phase3_document_ingestion")
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        assert {item["name"] for item in inspector.get_indexes("documents")} >= {
            "ix_documents_remote_file_id",
            "ix_documents_status",
        }
        assert {item["name"] for item in inspector.get_indexes("content_blobs")} >= {
            "ix_content_blobs_sha256"
        }
        assert {item["name"] for item in inspector.get_indexes("document_versions")} >= {
            "ix_document_versions_document_version"
        }
        assert {item["name"] for item in inspector.get_indexes("document_pages")} >= {
            "ix_document_pages_version_page"
        }
    finally:
        engine.dispose()


def test_phase3_schema_exposes_positive_id_and_finite_dimension_checks(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase3.db"
    command.upgrade(make_config(database_path), "0003_phase3_document_ingestion")
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        checks_by_table = {
            table: {constraint["name"] for constraint in inspector.get_check_constraints(table)}
            for table in DOCUMENT_TABLES
        }
        assert checks_by_table["documents"] >= {
            "ck_documents_id",
            "ck_documents_remote_file_id",
        }
        assert checks_by_table["content_blobs"] >= {"ck_content_blobs_id"}
        assert checks_by_table["document_versions"] >= {
            "ck_document_versions_id",
            "ck_document_versions_document_id",
            "ck_document_versions_content_blob_id",
        }
        assert checks_by_table["document_pages"] >= {
            "ck_document_pages_id",
            "ck_document_pages_document_version_id",
            "ck_document_pages_width_points_finite",
            "ck_document_pages_height_points_finite",
        }
    finally:
        engine.dispose()


def test_phase3_sqlite_schema_contains_no_pdf_blob_column(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    command.upgrade(make_config(database_path), "0003_phase3_document_ingestion")
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        all_columns = [
            column for table in DOCUMENT_TABLES for column in inspector.get_columns(table)
        ]
        assert all(not isinstance(column["type"], LargeBinary) for column in all_columns)
        assert {column["name"] for column in inspector.get_columns("content_blobs")} == {
            "id",
            "sha256",
            "size_bytes",
            "media_type",
            "storage_key",
            "created_at",
        }
    finally:
        engine.dispose()
