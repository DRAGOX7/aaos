"""Disposable migration tests for Phase 8 visual provenance."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

ROOT = Path(__file__).resolve().parents[1]


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(path))
    return config


def _revision(path: Path) -> str | None:
    engine = create_database_engine(path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_visual_provenance_upgrade_and_downgrade_are_additive(tmp_path: Path) -> None:
    path = tmp_path / "visual-migration.db"
    config = _config(path)
    command.upgrade(config, "0006_phase7e_task_records")
    before = set(inspect(create_database_engine(path)).get_table_names())
    command.upgrade(config, "0007_phase8_visual_provenance")
    engine = create_database_engine(path)
    try:
        inspector = inspect(engine)
        assert _revision(path) == "0007_phase8_visual_provenance"
        assert {"visual_analysis_runs", "visual_elements"}.issubset(
            set(inspector.get_table_names())
        )
        assert before.issubset(set(inspector.get_table_names()))
        assert "document_versions" in inspector.get_table_names()
    finally:
        engine.dispose()
    command.downgrade(config, "0006_phase7e_task_records")
    assert _revision(path) == "0006_phase7e_task_records"
    engine = create_database_engine(path)
    try:
        assert "visual_analysis_runs" not in inspect(engine).get_table_names()
        assert "visual_elements" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_visual_provenance_constraints_and_indexes_are_closed(tmp_path: Path) -> None:
    path = tmp_path / "visual-constraints.db"
    command.upgrade(_config(path), "0007_phase8_visual_provenance")
    engine = create_database_engine(path)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("visual_analysis_runs")} == {
            "id",
            "analysis_key",
            "attempt_number",
            "document_version_id",
            "page_number",
            "source_content_sha256",
            "render_sha256",
            "model_id",
            "model_revision",
            "prompt_revision",
            "selector_revision",
            "renderer_revision",
            "analysis_schema_revision",
            "status",
            "analysis_sha256",
            "created_at",
        }
        assert {column["name"] for column in inspector.get_columns("visual_elements")} == {
            "id",
            "analysis_run_id",
            "sequence",
            "visual_kind",
            "observed_text",
            "interpretation_text",
            "structured_payload",
            "payload_sha256",
            "confidence",
            "created_at",
        }
        run_fks = inspector.get_foreign_keys("visual_analysis_runs")
        element_fks = inspector.get_foreign_keys("visual_elements")
        assert run_fks[0]["referred_table"] == "document_versions"
        assert run_fks[0]["options"]["ondelete"] == "RESTRICT"
        assert element_fks[0]["referred_table"] == "visual_analysis_runs"
        assert element_fks[0]["options"]["ondelete"] == "CASCADE"
        index_names = {index["name"] for index in inspector.get_indexes("visual_analysis_runs")}
        assert "uq_visual_analysis_runs_succeeded_key" in index_names
        assert "ix_visual_analysis_runs_document_page_source" in index_names
    finally:
        engine.dispose()


def test_successful_result_index_excludes_partial_and_rejects_duplicate_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "visual-index.db"
    command.upgrade(_config(path), "0007_phase8_visual_provenance")
    engine = create_database_engine(path)
    try:
        with engine.begin() as connection:
            source_hash = "b" * 64
            connection.execute(
                text(
                    "INSERT INTO courses (id, source_system, source_id, title, code, "
                    "description, is_active) VALUES (1, 'fake', 'course-1', 'Course', "
                    "'C1', NULL, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO remote_files (id, course_id, source_system, source_id, title, "
                    "remote_url, is_active) VALUES (1, 1, 'fake', 'file-1', 'File', NULL, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO documents (id, remote_file_id, ingestion_status, created_at, "
                    "updated_at) VALUES (1, 1, 'READY', '2026-01-01T00:00:00Z', "
                    "'2026-01-01T00:00:00Z')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO content_blobs (id, sha256, size_bytes, media_type, "
                    "storage_key, created_at) VALUES (1, :sha, 1, 'application/pdf', :key, "
                    "'2026-01-01T00:00:00Z')"
                ),
                {"sha": source_hash, "key": f"sha256/{source_hash[:2]}/{source_hash}.pdf"},
            )
            connection.execute(
                text(
                    "INSERT INTO document_versions (id, document_id, version_number, "
                    "content_blob_id, source_filename, ingested_at, extraction_status, page_count) "
                    "VALUES (1, 1, 1, 1, 'file.pdf', '2026-01-01T00:00:00Z', 'SUCCESS', 1)"
                )
            )
            for attempt, status, analysis_hash in (
                (1, "PARTIAL", "c" * 64),
                (2, "PARTIAL", "d" * 64),
                (3, "SUCCEEDED", "e" * 64),
            ):
                connection.execute(
                    text(
                        "INSERT INTO visual_analysis_runs "
                        "(analysis_key, attempt_number, document_version_id, page_number, "
                        "source_content_sha256, render_sha256, model_id, model_revision, "
                        "prompt_revision, selector_revision, renderer_revision, "
                        "analysis_schema_revision, status, analysis_sha256, created_at) "
                        "VALUES (:key, :attempt, 1, 1, :source, :render, 'model', 'rev', "
                        "'prompt', 'selector', 'renderer', 'schema', :status, :analysis, "
                        "'2026-01-01T00:00:00Z')"
                    ),
                    {
                        "key": "a" * 64,
                        "attempt": attempt,
                        "source": "b" * 64,
                        "render": "c" * 64,
                        "status": status,
                        "analysis": analysis_hash,
                    },
                )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO visual_analysis_runs "
                        "(analysis_key, attempt_number, document_version_id, page_number, "
                        "source_content_sha256, render_sha256, model_id, model_revision, "
                        "prompt_revision, selector_revision, renderer_revision, "
                        "analysis_schema_revision, status, analysis_sha256, created_at) "
                        "VALUES (:key, 4, 1, 1, :source, :render, 'model', 'rev', 'prompt', "
                        "'selector', 'renderer', 'schema', 'SUCCEEDED', :analysis, "
                        "'2026-01-01T00:00:00Z')"
                    ),
                    {"key": "a" * 64, "source": "b" * 64, "render": "c" * 64, "analysis": "f" * 64},
                )
    finally:
        engine.dispose()
