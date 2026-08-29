"""Migration tests for the Phase 4 local-RAG persistence schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, text
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


def sqlite_master_names(connection: Connection) -> set[str]:
    return {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master"))}


def seed_phase3_provenance(connection: Connection) -> None:
    """Seed the smallest valid Phase 1/3 graph for RAG constraint tests."""
    blob_sha256 = "a" * 64
    connection.execute(
        text(
            "INSERT INTO courses (id, source_system, source_id, title, is_active) "
            "VALUES (1, 'test', 'course-1', 'Course', 1)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO remote_files (id, course_id, source_system, source_id, title, is_active) "
            "VALUES (1, 1, 'test', 'file-1', 'File', 1)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO documents (id, remote_file_id, ingestion_status, created_at, updated_at) "
            "VALUES (1, 1, 'READY', '2026-08-28T00:00:00Z', '2026-08-28T00:00:00Z')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO content_blobs (id, sha256, size_bytes, media_type, storage_key, created_at) "
            "VALUES (1, :sha256, 1, 'application/pdf', :storage_key, '2026-08-28T00:00:00Z')"
        ),
        {
            "sha256": blob_sha256,
            "storage_key": f"sha256/aa/{blob_sha256}.pdf",
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_versions "
            "(id, document_id, version_number, content_blob_id, source_filename, "
            "ingested_at, extraction_status, page_count) "
            "VALUES (1, 1, 1, 1, 'source.pdf', '2026-08-28T00:00:00Z', 'SUCCESS', 1)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO document_pages "
            "(id, document_version_id, page_number, text, text_source, width_points, "
            "height_points, created_at) "
            "VALUES (1, 1, 1, 'source text', 'EMBEDDED_PDF', 100.0, 100.0, "
            "'2026-08-28T00:00:00Z')"
        )
    )


def insert_generation(connection: Connection, **overrides: object) -> int:
    values = {
        "status": "BUILDING",
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
    values.update(overrides)
    result = connection.execute(
        text(
            "INSERT INTO rag_index_generations "
            "(status, config_fingerprint, chunker_version, chunking_config, "
            "lexical_normalizer_version, embedding_model, embedding_revision, "
            "embedding_dimensions, retrieval_config, content_revision, created_at) "
            "VALUES (:status, :config_fingerprint, :chunker_version, :chunking_config, "
            ":lexical_normalizer_version, :embedding_model, :embedding_revision, "
            ":embedding_dimensions, :retrieval_config, :content_revision, :created_at)"
        ),
        values,
    )
    return int(result.lastrowid)


def insert_chunk(connection: Connection, **overrides: object) -> int:
    values = {
        "chunk_key": "b" * 64,
        "generation_id": 1,
        "document_version_id": 1,
        "ordinal": 1,
        "heading": None,
        "exact_text": "chunk text",
        "token_count": 2,
        "text_sha256": "c" * 64,
        "page_start": 1,
        "page_end": 1,
        "created_at": "2026-08-28T00:00:00Z",
    }
    values.update(overrides)
    result = connection.execute(
        text(
            "INSERT INTO rag_chunks "
            "(chunk_key, generation_id, document_version_id, ordinal, heading, exact_text, "
            "token_count, text_sha256, page_start, page_end, created_at) "
            "VALUES (:chunk_key, :generation_id, :document_version_id, :ordinal, :heading, "
            ":exact_text, :token_count, :text_sha256, :page_start, :page_end, :created_at)"
        ),
        values,
    )
    return int(result.lastrowid)


def insert_document_index(connection: Connection, **overrides: object) -> int:
    values = {
        "generation_id": 1,
        "document_version_id": 1,
        "status": "READY",
        "coverage_status": "FULL",
        "searchable_page_count": 1,
        "chunk_count": 1,
        "error_code": None,
        "created_at": "2026-08-28T00:00:00Z",
        "updated_at": "2026-08-28T00:00:00Z",
    }
    values.update(overrides)
    result = connection.execute(
        text(
            "INSERT INTO rag_document_indexes "
            "(generation_id, document_version_id, status, coverage_status, "
            "searchable_page_count, chunk_count, error_code, created_at, updated_at) "
            "VALUES (:generation_id, :document_version_id, :status, :coverage_status, "
            ":searchable_page_count, :chunk_count, :error_code, :created_at, :updated_at)"
        ),
        values,
    )
    return int(result.lastrowid)


def insert_source_span(connection: Connection, **overrides: object) -> int:
    values: dict[str, object] = {
        "rag_chunk_id": 1,
        "document_page_id": 1,
        "ordinal": 1,
        "page_number": 1,
        "page_char_start": 0,
        "page_char_end": 5,
        "chunk_char_start": 0,
        "chunk_char_end": 5,
    }
    values.update(overrides)
    result = connection.execute(
        text(
            "INSERT INTO rag_chunk_source_spans "
            "(rag_chunk_id, document_page_id, ordinal, page_number, page_char_start, "
            "page_char_end, chunk_char_start, chunk_char_end) "
            "VALUES (:rag_chunk_id, :document_page_id, :ordinal, :page_number, "
            ":page_char_start, :page_char_end, :chunk_char_start, :chunk_char_end)"
        ),
        values,
    )
    return int(result.lastrowid)


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
            fts_sql = (
                connection.execute(
                    text("SELECT sql FROM sqlite_master WHERE name = 'rag_chunks_fts'")
                )
                .scalar_one()
                .lower()
            )
            assert fts_sql.startswith("create virtual table rag_chunks_fts using fts5")
            assert "normalized_text" in fts_sql
            assert "generation_id unindexed" in fts_sql
            assert "document_version_id unindexed" in fts_sql
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
        assert {
            item["name"] for item in inspector.get_check_constraints("rag_index_generations")
        } >= {
            "ck_rag_index_generations_status",
            "ck_rag_index_generations_embedding_dimensions",
            "ck_rag_index_generations_content_revision",
        }
        assert {
            item["name"] for item in inspector.get_check_constraints("rag_document_indexes")
        } >= {
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
        assert {
            item["name"] for item in inspector.get_check_constraints("rag_chunk_source_spans")
        } >= {
            "ck_rag_chunk_source_spans_page_bounds",
            "ck_rag_chunk_source_spans_chunk_bounds",
        }
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("rag_document_indexes")
        } == {
            "rag_index_generations",
            "document_versions",
        }
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("rag_chunks")
        } == {
            "rag_index_generations",
            "document_versions",
        }
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("rag_chunk_source_spans")
        } == {
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


def test_phase4_schema_rejects_invalid_rag_document_index_references_statuses_and_counts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            generation_id = insert_generation(connection)
        for overrides in (
            {"generation_id": 999},
            {"document_version_id": 999},
            {"status": "UNKNOWN"},
            {"coverage_status": "UNKNOWN"},
            {"searchable_page_count": -1},
            {"chunk_count": -1},
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    insert_document_index(
                        connection, **({"generation_id": generation_id} | overrides)
                    )
    finally:
        engine.dispose()


def test_phase4_schema_rejects_duplicate_generation_document_index_identity(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            generation_id = insert_generation(connection)
            insert_document_index(connection, generation_id=generation_id)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_document_index(connection, generation_id=generation_id)
    finally:
        engine.dispose()


def test_phase4_schema_rejects_invalid_generation_status_revision_and_fingerprint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        for overrides in (
            {"status": "UNKNOWN"},
            {"content_revision": -1},
            {"config_fingerprint": "not-a-sha256"},
            {"config_fingerprint": "a" * 63},
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    insert_generation(connection, **overrides)
    finally:
        engine.dispose()


def test_phase4_schema_rejects_invalid_chunk_identity_foreign_keys_and_bounds(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            generation_id = insert_generation(connection)
            insert_chunk(connection, generation_id=generation_id)
        for overrides in (
            {"generation_id": 999},
            {"document_version_id": 999},
            {"ordinal": 0},
            {"token_count": 0},
            {"page_start": 0},
            {"page_start": 2, "page_end": 1},
            {"chunk_key": "d" * 64},
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    insert_chunk(connection, **({"generation_id": generation_id} | overrides))
    finally:
        engine.dispose()


def test_phase4_schema_rejects_duplicate_global_chunk_key(tmp_path: Path) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            first_generation_id = insert_generation(connection)
            chunk_key = "b" * 64
            insert_chunk(
                connection,
                generation_id=first_generation_id,
                chunk_key=chunk_key,
            )
            second_generation_id = insert_generation(connection)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_chunk(
                    connection,
                    generation_id=second_generation_id,
                    ordinal=2,
                    chunk_key=chunk_key,
                )
    finally:
        engine.dispose()


def test_phase4_schema_rejects_invalid_source_span_references_bounds_and_ordering(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            generation_id = insert_generation(connection)
            chunk_id = insert_chunk(connection, generation_id=generation_id)
            insert_source_span(connection, rag_chunk_id=chunk_id)
        for overrides in (
            {"rag_chunk_id": 999},
            {"document_page_id": 999},
            {"ordinal": 0},
            {"page_char_end": 0},
            {"chunk_char_end": 0},
            {"ordinal": 1, "page_char_start": 5, "page_char_end": 10},
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    insert_source_span(connection, **({"rag_chunk_id": chunk_id} | overrides))
    finally:
        engine.dispose()


def test_phase4_fts5_uses_authoritative_chunk_rowids(tmp_path: Path) -> None:
    database_path = tmp_path / "phase4.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    try:
        with engine.begin() as connection:
            seed_phase3_provenance(connection)
            generation_id = insert_generation(connection)
            chunk_id = insert_chunk(connection, generation_id=generation_id)
            connection.execute(
                text(
                    "INSERT INTO rag_chunks_fts "
                    "(rowid, normalized_text, generation_id, document_version_id) "
                    "VALUES (:rowid, 'BERT YOLOv11', :generation_id, :document_version_id)"
                ),
                {
                    "rowid": chunk_id,
                    "generation_id": generation_id,
                    "document_version_id": 1,
                },
            )
            assert (
                connection.execute(
                    text("SELECT rowid FROM rag_chunks_fts WHERE rag_chunks_fts MATCH 'YOLOv11'")
                ).scalar_one()
                == chunk_id
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
