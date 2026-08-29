"""Deterministic lexical normalization and SQLite FTS5 retrieval tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentVersionORM,
)
from aaos.rag.contracts import (
    CourseScope,
    CoverageStatus,
    DocumentIndexStatus,
    RagErrorCode,
    RagOperationalError,
)
from aaos.rag.lexical import (
    LEXICAL_NORMALIZER_VERSION,
    LexicalSearchResult,
    normalize_lexical_text,
)
from aaos.rag.repository import ChunkRecord, GenerationBuild, QuerySnapshot, RagRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def repository_database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Engine]]:
    database_path = tmp_path / "rag-lexical.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, engine
    finally:
        engine.dispose()


def _seed_phase3(sessions: SessionFactory, *, course_count: int = 1) -> tuple[int, ...]:
    with sessions() as session, session.begin():
        versions: list[DocumentVersionORM] = []
        for course_number in range(course_count):
            course = CourseORM(
                source_system="phase3",
                source_id=f"course-{course_number}",
                title=f"Course {course_number}",
                is_active=True,
            )
            session.add(course)
            session.flush()
            remote = RemoteFileORM(
                course_id=course.id,
                source_system="phase3",
                source_id=f"remote-{course_number}",
                title=f"Remote {course_number}",
                is_active=True,
            )
            session.add(remote)
            session.flush()
            document = DocumentORM(
                remote_file_id=remote.id,
                ingestion_status=IngestionStatus.READY.value,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(document)
            session.flush()
            digest = (f"{course_number + 1:08x}").ljust(64, "a")
            blob = ContentBlobORM(
                sha256=digest,
                size_bytes=100,
                media_type="application/pdf",
                storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
                created_at=NOW,
            )
            session.add(blob)
            session.flush()
            version = DocumentVersionORM(
                document_id=document.id,
                version_number=1,
                content_blob_id=blob.id,
                source_filename=f"document-{course_number}.pdf",
                ingested_at=NOW,
                extraction_status="SUCCESS",
                page_count=1,
            )
            session.add(version)
            session.flush()
            session.add(
                DocumentPageORM(
                    document_version_id=version.id,
                    page_number=1,
                    text=f"source text {course_number}",
                    text_source="EMBEDDED_PDF",
                    width_points=612.0,
                    height_points=792.0,
                    created_at=NOW,
                )
            )
            versions.append(version)
        return tuple(int(version.id) for version in versions)


def _create_generation(
    repository: RagRepository, *, fingerprint: str = "a" * 64
) -> GenerationBuild:
    return repository.create_generation(
        config_fingerprint=fingerprint,
        chunker_version="chunk-v1",
        chunking_config="{}",
        lexical_normalizer_version=LEXICAL_NORMALIZER_VERSION,
        embedding_model="embedding-model",
        embedding_revision="embedding-revision",
        embedding_dimensions=3,
        retrieval_config="{}",
    )


def _ready_index(
    repository: RagRepository, generation: GenerationBuild, document_version_id: int
) -> None:
    repository.insert_document_index(
        generation.identity,
        expected_revision=0,
        document_version_id=document_version_id,
        status=DocumentIndexStatus.READY,
        coverage_status=CoverageStatus.FULL,
        searchable_page_count=1,
        chunk_count=1,
    )


def _insert_chunk(
    repository: RagRepository,
    generation: GenerationBuild,
    document_version_id: int,
    key: str,
    text_value: str,
) -> ChunkRecord:
    return repository.insert_chunk(
        generation.identity,
        expected_revision=0,
        document_version_id=document_version_id,
        chunk_key=key,
        ordinal=1,
        heading=None,
        exact_text=text_value,
        token_count=len(text_value.split()),
        text_sha256=(key[:64]).ljust(64, "0"),
        page_start=1,
        page_end=1,
    )


def _snapshot(generation: GenerationBuild, document_version_ids: tuple[int, ...]) -> QuerySnapshot:
    return QuerySnapshot(
        generation=generation.identity,
        scope=CourseScope(1),
        document_version_ids=document_version_ids,
    )


def test_normalizer_is_deterministic_and_conservative() -> None:
    value = "  ＢＥＲＴ\t  العربية   mAP50-95  "
    assert normalize_lexical_text(value) == "bert العربية map50-95"
    assert normalize_lexical_text(value) == normalize_lexical_text(value)


def test_normalizer_rejects_non_text() -> None:
    with pytest.raises(ValueError, match="text"):
        normalize_lexical_text(12)  # type: ignore[arg-type]


def test_technical_terms_and_arabic_are_searchable_without_mutating_exact_text(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        _ready_index(repository, generation, version_id)
        exact_text = "BERT YOLOv11 mAP50-95 CNN SAHI DBSCAN العربية"
        chunk = _insert_chunk(repository, generation, version_id, "a" * 64, exact_text)
        snapshot = repository.pin_query_snapshot(CourseScope(1))

        assert isinstance(repository.lexical_search("BERT", snapshot), LexicalSearchResult)
        for query in ("BERT", "YOLOv11", "mAP50-95", "CNN", "SAHI", "DBSCAN", "العربية"):
            result = repository.lexical_search(query, snapshot)
            assert result.hits and result.hits[0].chunk_key == chunk.chunk_key

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT exact_text FROM rag_chunks WHERE id = :id"), {"id": chunk.chunk_id}
                ).scalar_one()
                == exact_text
            )


def test_fts_rows_are_created_and_deleted_with_authoritative_chunks(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        _ready_index(repository, generation, version_id)
        chunk = _insert_chunk(repository, generation, version_id, "b" * 64, "BERT retrieval")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM rag_chunks_fts")).scalar_one() == 1
        repository.delete_chunk(generation.identity, chunk.chunk_id, expected_revision=0)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM rag_chunks_fts")).scalar_one() == 0


def test_rebuild_fts_is_idempotent_and_repairs_missing_rows(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        chunk = _insert_chunk(repository, generation, version_id, "c" * 64, "BERT retrieval")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM rag_chunks_fts"))
        assert repository.rebuild_fts(generation.identity, expected_revision=0) == 1
        assert repository.rebuild_fts(generation.identity, expected_revision=0) == 1
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM rag_chunks_fts")).scalar_one() == 1
            assert (
                connection.execute(text("SELECT rowid FROM rag_chunks_fts")).scalar_one()
                == chunk.chunk_id
            )


def test_lexical_search_uses_bm25_ordering_and_empty_no_match_are_safe(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        _ready_index(repository, generation, version_id)
        first = _insert_chunk(repository, generation, version_id, "d" * 64, "BERT BERT BERT")
        second = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            chunk_key="e" * 64,
            ordinal=2,
            heading=None,
            exact_text="BERT",
            token_count=1,
            text_sha256="f" * 64,
            page_start=1,
            page_end=1,
        )
        snapshot = _snapshot(generation, (version_id,))
        result = repository.lexical_search("BERT", snapshot, limit=2)
        assert result.hits[0].chunk_key == first.chunk_key
        assert tuple(hit.rank for hit in result.hits) == (1, 2)
        assert result.hits[0].bm25_score <= result.hits[1].bm25_score
        assert repository.lexical_search("   ", snapshot).hits == ()
        assert repository.lexical_search("absent", snapshot).hits == ()
        assert second.chunk_key != first.chunk_key


def test_generation_and_authorized_document_filters_are_enforced(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        version_one, version_two = _seed_phase3(sessions, course_count=2)
        generation_one = _create_generation(repository, fingerprint="1" * 64)
        generation_two = _create_generation(repository, fingerprint="2" * 64)
        _ready_index(repository, generation_one, version_one)
        _ready_index(repository, generation_two, version_two)
        _insert_chunk(repository, generation_one, version_one, "1" * 64, "shared phrase")
        _insert_chunk(repository, generation_two, version_two, "2" * 64, "shared phrase")
        first = repository.lexical_search("shared", _snapshot(generation_one, (version_one,)))
        second = repository.lexical_search("shared", _snapshot(generation_two, (version_two,)))
        assert tuple(hit.chunk_key for hit in first.hits) == ("1" * 64,)
        assert tuple(hit.chunk_key for hit in second.hits) == ("2" * 64,)
        restricted = repository.lexical_search("shared", _snapshot(generation_one, (version_two,)))
        assert restricted.hits == ()


def test_ready_state_and_stale_fts_rows_cannot_authorize_results(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        chunk = _insert_chunk(repository, generation, version_id, "3" * 64, "shared phrase")
        snapshot = _snapshot(generation, (version_id,))
        assert repository.lexical_search("shared", snapshot).hits == ()
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=1,
        )
        assert repository.lexical_search("shared", snapshot).hits
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO rag_chunks_fts(rowid, normalized_text, generation_id, "
                    "document_version_id) VALUES (999999, 'shared', :generation_id, :version_id)"
                ),
                {"generation_id": generation.generation_id, "version_id": version_id},
            )
        result = repository.lexical_search("shared", snapshot)
        assert tuple(hit.chunk_key for hit in result.hits) == (chunk.chunk_key,)


def test_fts_failure_is_typed_and_authoritative_insert_rolls_back(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE rag_chunks_fts"))
        with pytest.raises(RagOperationalError) as caught:
            _insert_chunk(repository, generation, version_id, "4" * 64, "failure")
        assert caught.value.code is RagErrorCode.FTS_INDEX_FAILED
        assert repository.list_chunks(generation.identity) == ()


def test_lexical_search_rejects_invalid_snapshot_and_limit(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        (version_id,) = _seed_phase3(sessions)
        generation = _create_generation(repository)
        snapshot = _snapshot(generation, (version_id,))
        with pytest.raises(RagOperationalError) as caught:
            repository.lexical_search("query", snapshot, limit=0)
        assert caught.value.code is RagErrorCode.LEXICAL_INDEX_FAILED
