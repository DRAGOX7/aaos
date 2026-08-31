"""Crash/retry and lifecycle recovery tests for Phase 4 RAG."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

import aaos.rag.repository as rag_repository
from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentVersionORM,
)
from aaos.rag.chunking import DeterministicChunker
from aaos.rag.config import RagIndexConfig, compute_config_fingerprint
from aaos.rag.contracts import DocumentIndexStatus, RagErrorCode, RagOperationalError
from aaos.rag.dense import InMemoryDenseIndexFactory
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.indexing import RagDocumentIndexer, RagIndexingCoordinator
from aaos.rag.lock import RagIndexLock
from aaos.rag.repository import GenerationBuild, RagRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def _rag_config() -> RagIndexConfig:
    return RagIndexConfig(
        16,
        1,
        16,
        24,
        2,
        5,
        5,
        60,
        5,
        100,
        "chunk-v1",
        "lex-v1",
        "policy-v1",
        "fake-embedding-service",
        "test-only-v1",
        3,
    )


@contextmanager
def _database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Path]]:
    database_path = tmp_path / "recovery.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, database_path
    finally:
        engine.dispose()


def _seed(sessions: SessionFactory) -> int:
    with sessions() as session, session.begin():
        course = CourseORM(source_system="phase3", source_id="recovery-course", title="Course")
        session.add(course)
        session.flush()
        remote = RemoteFileORM(
            course_id=course.id,
            source_system="phase3",
            source_id="recovery-file",
            title="Recovery file",
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
        digest = sha256(b"recovery-content").hexdigest()
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
            source_filename="recovery.pdf",
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
                text="A recoverable document contains enough searchable evidence.",
                text_source="EMBEDDED_PDF",
                width_points=612.0,
                height_points=792.0,
                created_at=NOW,
            )
        )
        return int(version.id)


def _generation(repository: RagRepository) -> GenerationBuild:
    config = _rag_config()
    return repository.create_generation(
        config_fingerprint=compute_config_fingerprint(config),
        chunker_version=config.chunker_version,
        chunking_config="{}",
        lexical_normalizer_version=config.lexical_normalizer_version,
        embedding_model=config.embedding_model_identifier,
        embedding_revision=config.embedding_model_revision,
        embedding_dimensions=config.embedding_dimensions,
        retrieval_config="{}",
    )


def _coordinator(
    tmp_path: Path, repository: RagRepository, dense: InMemoryDenseIndexFactory
) -> RagIndexingCoordinator:
    config = _rag_config()
    indexer = RagDocumentIndexer(
        repository,
        DeterministicChunker(config),
        FakeEmbeddingService(dimensions=3),
        dense,
        RagIndexLock(tmp_path / "recovery.lock"),
    )
    return RagIndexingCoordinator(repository, indexer, RagIndexLock(tmp_path / "recovery.lock"))


def test_rebuild_retry_converges_after_sqlite_preparation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _database_path):
        version_id = _seed(sessions)
        initial = _generation(repository)
        repository.activate_generation(initial.identity, expected_revision=0)
        dense = InMemoryDenseIndexFactory()
        coordinator = _coordinator(tmp_path, repository, dense)
        original = repository.begin_document_preparation
        calls = 0

        def fail_once(*args: object, **kwargs: object) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            return cast(Any, original)(*args, **kwargs)

        monkeypatch.setattr(repository, "begin_document_preparation", fail_once)
        with pytest.raises(RagOperationalError) as caught:
            coordinator.rebuild()
        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        assert repository.get_active_generation().generation_id == initial.generation_id

        result = coordinator.rebuild()
        assert result.safe_status == "PASS"
        active = repository.get_active_generation()
        assert active.generation_id == result.generation_id
        assert (
            repository.get_document_index(active.identity, version_id).status
            is DocumentIndexStatus.READY
        )


def test_failed_dense_write_keeps_active_generation_queryable_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _database_path):
        version_id = _seed(sessions)
        initial = _generation(repository)
        repository.activate_generation(initial.identity, expected_revision=0)
        dense = InMemoryDenseIndexFactory()
        coordinator = _coordinator(tmp_path, repository, dense)
        original_open = dense.open
        failed = True

        class _FailingIndex:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            def upsert(self, records: Any) -> None:
                nonlocal failed
                if failed:
                    failed = False
                    raise RuntimeError("dense failure")
                self.inner.upsert(records)

            def search(self, *args: Any, **kwargs: Any) -> Any:
                return self.inner.search(*args, **kwargs)

            def contains(self, records: Any) -> Any:
                return self.inner.contains(records)

            def count(self) -> int:
                return int(self.inner.count())

            def close(self) -> None:
                self.inner.close()

        monkeypatch.setattr(
            dense, "open", lambda generation: _FailingIndex(original_open(generation))
        )
        with pytest.raises(RagOperationalError) as caught:
            coordinator.rebuild()
        assert caught.value.code is RagErrorCode.DENSE_INDEX_WRITE_FAILED
        assert repository.get_active_generation().generation_id == initial.generation_id

        result = coordinator.rebuild()
        assert result.safe_status == "PASS"
        assert (
            repository.get_document_index(
                repository.get_active_generation().identity, version_id
            ).status
            is DocumentIndexStatus.READY
        )


def test_active_index_new_publication_failure_is_retryable_without_revision_churn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _database_path):
        version_id = _seed(sessions)
        initial = _generation(repository)
        active = repository.activate_generation(initial.identity, expected_revision=0)
        dense = InMemoryDenseIndexFactory()
        coordinator = _coordinator(tmp_path, repository, dense)
        original = repository.publish_staged_documents

        def fail_publish(*args: object, **kwargs: object) -> Any:
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)

        monkeypatch.setattr(repository, "publish_staged_documents", fail_publish)
        with pytest.raises(RagOperationalError) as caught:
            coordinator.index_new()
        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        assert repository.get_active_generation().identity == active.identity
        assert (
            repository.get_document_index(active.identity, version_id).status
            is DocumentIndexStatus.PREPARING
        )

        monkeypatch.setattr(repository, "publish_staged_documents", original)
        result = coordinator.index_new()
        assert result.safe_status == "PASS"
        assert result.content_revision == active.identity.content_revision + 1
        assert (
            repository.get_document_index(
                repository.get_active_generation().identity, version_id
            ).status
            is DocumentIndexStatus.READY
        )


@pytest.mark.parametrize("boundary", ["before_fts", "before_ready", "before_activation"])
def test_rebuild_failure_matrix_never_publishes_partial_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    with _database(tmp_path) as (repository, sessions, _database_path):
        _seed(sessions)
        initial = _generation(repository)
        repository.activate_generation(initial.identity, expected_revision=0)
        dense = InMemoryDenseIndexFactory()
        coordinator = _coordinator(tmp_path, repository, dense)

        if boundary == "before_fts":

            def fail_fts(*args: object, **kwargs: object) -> Any:
                raise RagOperationalError(RagErrorCode.FTS_INDEX_FAILED)

            monkeypatch.setattr(rag_repository, "insert_fts_row", fail_fts)
        elif boundary == "before_ready":

            def fail_ready(*args: object, **kwargs: object) -> Any:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)

            monkeypatch.setattr(repository, "finish_document_preparation", fail_ready)
        else:

            def fail_activation(*args: object, **kwargs: object) -> Any:
                raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)

            monkeypatch.setattr(repository, "activate_generation", fail_activation)

        with pytest.raises(RagOperationalError):
            coordinator.rebuild()
        assert repository.get_active_generation().generation_id == initial.generation_id
        assert len(repository.list_building_generations()) == 1
