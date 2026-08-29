"""Migration tests for the Phase 4 local-RAG persistence schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAG_TABLES = {
    "rag_index_generations",
    "rag_document_indexes",
    "rag_chunks",
    "rag_chunk_source_spans",
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


def sqlite_master_names(connection: object) -> set[str]:
    return {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master"))  # type: ignore[attr-defined]
    }


def test_phase4_upgrade_from_phase3_adds_rag_tables_and_fts5(tmp_path: Path) -> None:
    database_path = tmp_path / "phase4.db"
    config = make_config(database_path)
    command.upgrade(config, "0003_phase3_document_ingestion")

    command.upgrade(config, "head")

    assert revision(database_path) == "0004_phase4_local_rag"
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) >= RAG_TABLES
        with engine.connect() as connection:
            assert "rag_chunks_fts" in sqlite_master_names(connection)
            assert connection.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'rag_chunks_fts'")
            ).scalar_one().lower().startswith("create virtual table rag_chunks_fts using fts5")
    finally:
        engine.dispose()


def test_phase4_schema_enforces_named_constraints_indexes_and_foreign_keys(tmp_path: Path) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        assert {item["name"] for item in inspector.get_indexes("rag_index_generations")} >= {
            "uq_rag_index_generations_one_active"
        }
        assert {item["name"] for item in inspector.get_indexes("rag_chunks")} >= {
            "ix_rag_chunks_generation_document_ordinal"
        }
        assert {item["name"] for item in inspector.get_indexes("rag_chunk_source_spans")} >= {
            "ix_rag_chunk_source_spans_chunk_ordinal"
        }
        assert {item["name"] for item in inspector.get_check_constraints("rag_index_generations")} >= {
            "ck_rag_index_generations_status",
            "ck_rag_index_generations_embedding_dimensions",
            "ck_rag_index_generations_content_revision",
        }
        assert {item["name"] for item in inspector.get_check_constraints("rag_document_indexes")} >= {
            "ck_rag_document_indexes_status",
            "ck_rag_document_indexes_coverage_status",
            "ck_rag_document_indexes_searchable_page_count",
            "ck_rag_document_indexes_chunk_count",
        }
        assert {item["name"] for item in inspector.get_check_constraints("rag_chunks")} >= {
            "ck_rag_chunks_ordinal",
            "ck_rag_chunks_token_count",
            "ck_rag_chunks_page_bounds",
        }
        assert {item["name"] for item in inspector.get_check_constraints("rag_chunk_source_spans")} >= {
            "ck_rag_chunk_source_spans_page_bounds",
            "ck_rag_chunk_source_spans_chunk_bounds",
        }
        assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("rag_document_indexes")} == {
            "rag_index_generations",
            "document_versions",
        }
        assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("rag_chunks")} == {
            "rag_index_generations",
            "document_versions",
        }
        assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("rag_chunk_source_spans")} == {
            "rag_chunks",
            "document_pages",
        }
    finally:
        engine.dispose()


def test_phase4_schema_rejects_invalid_generation_and_duplicate_active_generation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            valid = {
                "status": "ACTIVE",
                "config_fingerprint": "a" * 64,
                "chunker_version": "v1",
                "chunking_config": "{}",
                "lexical_normalizer_version": "v1",
                "embedding_model": "model",
                "embedding_revision": "revision",
                "embedding_dimensions": 1024,
                "retrieval_config": "{}",
                "content_revision": 0,
                "created_at": "2026-08-28T00:00:00Z",
            }
            connection.execute(
                text(
                    "INSERT INTO rag_index_generations "
                    "(status, config_fingerprint, chunker_version, chunking_config, "
                    "lexical_normalizer_version, embedding_model, embedding_revision, "
                    "embedding_dimensions, retrieval_config, content_revision, created_at) "
                    "VALUES (:status, :config_fingerprint, :chunker_version, :chunking_config, "
                    ":lexical_normalizer_version, :embedding_model, :embedding_revision, "
                    ":embedding_dimensions, :retrieval_config, :content_revision, :created_at)"
                ),
                valid,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO rag_index_generations "
                        "(status, config_fingerprint, chunker_version, chunking_config, "
                        "lexical_normalizer_version, embedding_model, embedding_revision, "
                        "embedding_dimensions, retrieval_config, content_revision, created_at) "
                        "VALUES ('ACTIVE', :fingerprint, 'v1', '{}', 'v1', 'model', 'revision', "
                        "1024, '{}', 0, '2026-08-28T00:00:00Z')"
                    ),
                    {"fingerprint": "b" * 64},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO rag_index_generations "
                        "(status, config_fingerprint, chunker_version, chunking_config, "
                        "lexical_normalizer_version, embedding_model, embedding_revision, "
                        "embedding_dimensions, retrieval_config, content_revision, created_at) "
                        "VALUES ('BUILDING', :fingerprint, 'v1', '{}', 'v1', 'model', 'revision', "
                        "0, '{}', 0, '2026-08-28T00:00:00Z')"
                    ),
                    {"fingerprint": "c" * 64},
                )
    finally:
        engine.dispose()


def test_phase4_downgrade_to_phase3_removes_rag_tables_and_fts5(tmp_path: Path) -> None:
    database_path = tmp_path / "phase4.db"
    config = make_config(database_path)
    command.upgrade(config, "head")

    command.downgrade(config, "0003_phase3_document_ingestion")

    assert revision(database_path) == "0003_phase3_document_ingestion"
    engine = create_database_engine(database_path)
    try:
        assert not (set(inspect(engine).get_table_names()) & RAG_TABLES)
        with engine.connect() as connection:
            assert "rag_chunks_fts" not in sqlite_master_names(connection)
    finally:
        engine.dispose()
