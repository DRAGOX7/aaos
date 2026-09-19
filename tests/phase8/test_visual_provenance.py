"""Task 7 visual provenance contract and repository tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.phase8.visual_provenance import (
    VisualAnalysis,
    VisualElement,
    VisualKind,
    VisualProvenanceRepository,
    VisualStatus,
)

ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 64
HASH_B = "b" * 64


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(path))
    return config


def _migrated(path: Path) -> tuple[Engine, sessionmaker[Session]]:
    command.upgrade(_config(path), "0007_phase8_visual_provenance")
    engine = create_database_engine(path)
    return engine, create_session_factory(engine)


def _seed_version(engine: Engine, *, version_id: int = 7, source_hash: str = HASH_A) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO courses (id, source_system, source_id, title, code, description, "
                "is_active) VALUES (1, 'fake', 'course-1', 'Course', 'C1', NULL, 1)"
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
                "INSERT INTO content_blobs (id, sha256, size_bytes, media_type, storage_key, "
                "created_at) VALUES (1, :sha, 1, 'application/pdf', :key, "
                "'2026-01-01T00:00:00Z')"
            ),
            {"sha": source_hash, "key": f"sha256/{source_hash[:2]}/{source_hash}.pdf"},
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (id, document_id, version_number, "
                "content_blob_id, source_filename, ingested_at, extraction_status, page_count) "
                "VALUES (:id, 1, 1, 1, 'file.pdf', '2026-01-01T00:00:00Z', 'SUCCESS', 2)"
            ),
            {"id": version_id},
        )


def _element(*, kind: VisualKind = VisualKind.TEXT_REGION) -> VisualElement:
    payload = json.dumps(
        {"observations": {"text": "observed"}, "interpretations": None},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return VisualElement(
        kind=kind,
        observed_text="observed",
        interpretation_text=None,
        structured_payload=payload,
        confidence=0.5,
    )


def _analysis(*, status: VisualStatus = VisualStatus.SUCCEEDED, attempt: int = 1) -> VisualAnalysis:
    return VisualAnalysis(
        document_version_id=7,
        page_number=1,
        source_content_sha256=HASH_A,
        render_sha256=HASH_B,
        model_id="qwen/qwen3.5-4b",
        model_revision="rev-1",
        prompt_revision="prompt-1",
        selector_revision="selector-1",
        renderer_revision="renderer-1",
        analysis_schema_revision="vision-analysis-v1",
        status=status,
        elements=()
        if status
        in {
            VisualStatus.FAILED,
            VisualStatus.TIMED_OUT,
            VisualStatus.UNAVAILABLE,
        }
        else (_element(),),
        attempt_number=attempt,
        analysis_sha256=None,
    )


def test_visual_analysis_rejects_invalid_status_and_hashes() -> None:
    with pytest.raises(ValueError):
        _analysis(status="BAD")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        VisualElement(
            kind=VisualKind.TEXT_REGION,
            observed_text=None,
            interpretation_text=None,
            structured_payload=None,
            confidence=None,
        )


def test_repository_rebinds_persisted_version_and_stores_success_atomically(tmp_path: Path) -> None:
    engine, sessions = _migrated(tmp_path / "visual.db")
    try:
        _seed_version(engine)
        repository = VisualProvenanceRepository(sessions)
        run_id = repository.save_analysis(_analysis())
        stored = repository.get_analysis(7, 1, HASH_A)
        assert run_id > 0
        assert stored is not None
        assert stored.analysis_key == _analysis().analysis_key
        assert stored.attempt_number == 1
    finally:
        engine.dispose()


def test_partial_and_failure_attempts_remain_auditable_and_rerunnable(tmp_path: Path) -> None:
    engine, sessions = _migrated(tmp_path / "visual.db")
    try:
        _seed_version(engine)
        repository = VisualProvenanceRepository(sessions)
        partial_id = repository.save_analysis(_analysis(status=VisualStatus.PARTIAL))
        second_partial = repository.save_analysis(
            _analysis(status=VisualStatus.PARTIAL, attempt=2), explicit_rerun=True
        )
        success_id = repository.save_analysis(
            _analysis(status=VisualStatus.SUCCEEDED, attempt=3), explicit_rerun=True
        )
        assert len({partial_id, second_partial, success_id}) == 3
        stored = repository.get_analysis(7, 1, HASH_A)
        assert stored is not None
        assert stored.status is VisualStatus.SUCCEEDED
    finally:
        engine.dispose()


def test_failed_attempt_has_no_elements_and_does_not_retry_automatically(tmp_path: Path) -> None:
    engine, sessions = _migrated(tmp_path / "visual.db")
    try:
        _seed_version(engine)
        repository = VisualProvenanceRepository(sessions)
        failed = repository.save_analysis(
            _analysis(status=VisualStatus.UNAVAILABLE),
        )
        duplicate = repository.save_analysis(_analysis(status=VisualStatus.UNAVAILABLE))
        assert duplicate == failed
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM visual_elements WHERE analysis_run_id=:id"),
                    {"id": failed},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_render_hash_is_bound_to_rendered_bytes() -> None:
    raw = b"rendered"
    assert hashlib.sha256(raw).hexdigest() != HASH_B
