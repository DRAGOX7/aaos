"""Per-document cross-store indexing tests using disposable fakes only."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
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
from aaos.rag.chunking import DeterministicChunker
from aaos.rag.config import RagIndexConfig, compute_config_fingerprint
from aaos.rag.contracts import (
    CoverageStatus,
    DenseIndex,
    DenseIndexFactory,
    DenseSearchHit,
    DenseVectorRecord,
    DocumentIndexStatus,
    EmbeddingService,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)
from aaos.rag.dense import InMemoryDenseIndexFactory
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.indexing import RagDocumentIndexer
from aaos.rag.lock import RagIndexLock
from aaos.rag.repository import DocumentVerificationRecord, GenerationBuild, RagRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def _database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Engine]]:
    database_path = tmp_path / "rag-indexing.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, engine
    finally:
        engine.dispose()


def _seed(sessions: SessionFactory, page_texts: tuple[str, ...]) -> int:
    with sessions() as session, session.begin():
        digest = sha256("\\n".join(page_texts).encode("utf-8")).hexdigest()
        source_id = digest[:12]
        course = CourseORM(
            source_system="test", source_id=f"course-{source_id}", title="Course", is_active=True
        )
        session.add(course)
        session.flush()
        remote = RemoteFileORM(
            course_id=course.id,
            source_system="test",
            source_id=f"remote-{source_id}",
            title="File",
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
            source_filename="file.pdf",
            ingested_at=NOW,
            extraction_status="SUCCESS",
            page_count=len(page_texts),
        )
        session.add(version)
        session.flush()
        session.add_all(
            DocumentPageORM(
                document_version_id=version.id,
                page_number=index,
                text=text,
                text_source="EMBEDDED_PDF",
                width_points=612.0,
                height_points=792.0,
                created_at=NOW,
            )
            for index, text in enumerate(page_texts, start=1)
        )
        return int(version.id)


def _chunker() -> DeterministicChunker:
    return DeterministicChunker(
        RagIndexConfig(
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
            "fake",
            "revision",
            3,
        )
    )


def _generation(repository: RagRepository) -> GenerationBuild:
    config = RagIndexConfig(
        16, 1, 16, 24, 2, 5, 5, 60, 5, 100, "chunk-v1", "lex-v1", "policy-v1", "fake", "revision", 3
    )
    return repository.create_generation(
        config_fingerprint=compute_config_fingerprint(config),
        chunker_version="chunk-v1",
        chunking_config="{}",
        lexical_normalizer_version="lex-v1",
        embedding_model="fake-embedding-service",
        embedding_revision="test-only-v1",
        embedding_dimensions=3,
        retrieval_config="{}",
    )


def _indexer(
    tmp_path: Path,
    repository: RagRepository,
    *,
    embeddings: EmbeddingService | None = None,
    dense: DenseIndexFactory | None = None,
) -> RagDocumentIndexer:
    return RagDocumentIndexer(
        repository,
        _chunker(),
        embeddings or FakeEmbeddingService(dimensions=3),
        dense or InMemoryDenseIndexFactory(),
        RagIndexLock(tmp_path / "phase4.lock"),
    )


def test_prepare_document_is_ready_only_after_cross_store_verification(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(
            sessions, ("A searchable page with useful tokens.", "Another searchable page.")
        )
        generation = _generation(repository)
        result = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert result.status is DocumentIndexStatus.READY
        assert result.coverage_status is CoverageStatus.FULL
        assert (
            repository.verify_generation_document(generation.identity, version_id)
            == result.verification
        )
        assert (
            repository.get_document_index(generation.identity, version_id).status
            is DocumentIndexStatus.READY
        )


def test_prepare_document_records_partial_and_empty_coverage(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        partial_id = _seed(sessions, ("searchable text", "   "))
        empty_id = _seed(sessions, ("  ",))
        generation = _generation(repository)
        indexer = _indexer(tmp_path, repository)
        partial = indexer.prepare_document(generation.identity, partial_id)
        empty = indexer.prepare_document(generation.identity, empty_id)
        assert (partial.status, partial.coverage_status) == (
            DocumentIndexStatus.READY,
            CoverageStatus.PARTIAL,
        )
        assert (empty.status, empty.coverage_status, empty.chunk_count) == (
            DocumentIndexStatus.NOT_SEARCHABLE,
            CoverageStatus.NONE,
            0,
        )


class _BrokenEmbeddings(FakeEmbeddingService):
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)


class _WrongCardinalityEmbeddings(FakeEmbeddingService):
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()


class _WrongDimensionEmbeddings(FakeEmbeddingService):
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.0, 1.0) for _ in texts)


class _BrokenDenseFactory:
    def open(self, generation: RagGenerationIdentity) -> DenseIndex:
        raise RuntimeError("C:/private/dense failure")

    def remove(self, generation: RagGenerationIdentity) -> None:
        del generation


class _ControlledDenseIndex:
    def __init__(
        self,
        inner: DenseIndex,
        *,
        fail_upsert: bool,
        contains_result: bool | None,
    ) -> None:
        self._inner = inner
        self._fail_upsert = fail_upsert
        self._contains_result = contains_result

    def upsert(self, records: tuple[DenseVectorRecord, ...]) -> None:
        if self._fail_upsert:
            if records:
                self._inner.upsert(records[:1])
            raise RuntimeError("private dense upsert failure")
        self._inner.upsert(records)

    def search(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
        allowed_document_version_ids: tuple[int, ...],
    ) -> tuple[DenseSearchHit, ...]:
        return self._inner.search(
            vector,
            limit=limit,
            allowed_document_version_ids=allowed_document_version_ids,
        )

    def contains(self, chunk_keys: tuple[str, ...]) -> bool:
        if self._contains_result is not None:
            return self._contains_result
        return self._inner.contains(chunk_keys)

    def count(self) -> int:
        return self._inner.count()

    def close(self) -> None:
        self._inner.close()


class _ControlledDenseFactory:
    def __init__(self, *, fail_upsert: bool = False, contains_result: bool | None = None) -> None:
        self._inner = InMemoryDenseIndexFactory()
        self.fail_upsert = fail_upsert
        self.contains_result = contains_result

    def open(self, generation: RagGenerationIdentity) -> DenseIndex:
        return _ControlledDenseIndex(
            self._inner.open(generation),
            fail_upsert=self.fail_upsert,
            contains_result=self.contains_result,
        )

    def remove(self, generation: RagGenerationIdentity) -> None:
        self._inner.remove(generation)

    def stored_count(self, generation: RagGenerationIdentity) -> int:
        index = self._inner.open(generation)
        try:
            return index.count()
        finally:
            index.close()


def test_prepare_document_failure_is_safe_compensated_and_retryable(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        failed = _indexer(
            tmp_path, repository, embeddings=_BrokenEmbeddings(dimensions=3)
        ).prepare_document(generation.identity, version_id)
        assert (failed.status, failed.error_code) == (
            DocumentIndexStatus.FAILED,
            RagErrorCode.EMBEDDING_FAILED,
        )
        assert repository.list_chunks(generation.identity, version_id) == ()
        retried = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert retried.status is DocumentIndexStatus.READY


def test_prepare_document_never_mutates_phase3_rows(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, engine):
        version_id = _seed(sessions, ("searchable text",))
        with engine.connect() as connection:
            before = tuple(
                connection.execute(
                    select(DocumentORM.id, DocumentORM.updated_at).order_by(DocumentORM.id)
                )
            )
        generation = _generation(repository)
        _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        with engine.connect() as connection:
            after = tuple(
                connection.execute(
                    select(DocumentORM.id, DocumentORM.updated_at).order_by(DocumentORM.id)
                )
            )
        assert after == before


@pytest.mark.parametrize(
    "embeddings",
    [_WrongCardinalityEmbeddings(dimensions=3), _WrongDimensionEmbeddings(dimensions=3)],
)
def test_embedding_shape_failures_are_safe_and_invisible(
    tmp_path: Path, embeddings: FakeEmbeddingService
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        result = _indexer(tmp_path, repository, embeddings=embeddings).prepare_document(
            generation.identity, version_id
        )
        assert result.status is DocumentIndexStatus.FAILED
        assert result.error_code is RagErrorCode.EMBEDDING_FAILED
        assert repository.list_chunks(generation.identity, version_id) == ()


def test_chunk_persistence_failure_is_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        monkeypatch.setattr(
            repository,
            "insert_chunk",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            ),
        )
        result = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert result.status is DocumentIndexStatus.FAILED
        assert repository.list_chunks(generation.identity, version_id) == ()


def test_fts_failure_is_compensated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        monkeypatch.setattr(
            repository,
            "insert_chunk",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RagOperationalError(RagErrorCode.FTS_INDEX_FAILED)
            ),
        )
        result = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert (result.status, result.error_code) == (
            DocumentIndexStatus.FAILED,
            RagErrorCode.FTS_INDEX_FAILED,
        )


def test_dense_open_failure_is_safe(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        result = _indexer(tmp_path, repository, dense=_BrokenDenseFactory()).prepare_document(
            generation.identity, version_id
        )
        assert (result.status, result.error_code) == (
            DocumentIndexStatus.FAILED,
            RagErrorCode.DENSE_INDEX_WRITE_FAILED,
        )


def test_generation_mismatch_is_safe_and_creates_no_index(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        wrong = generation.identity.__class__(generation.generation_id, "a" * 64, 0, 3)
        with pytest.raises(RagOperationalError) as caught:
            _indexer(tmp_path, repository).prepare_document(wrong, version_id)
        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        with pytest.raises(RagOperationalError):
            repository.get_document_index(generation.identity, version_id)


def test_lock_contention_prevents_mutation(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        with lock.acquire(), pytest.raises(RagOperationalError) as caught:
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ).prepare_document(generation.identity, version_id)
        assert caught.value.code is RagErrorCode.RAG_INDEX_LOCKED
        with pytest.raises(RagOperationalError):
            repository.get_document_index(generation.identity, version_id)


@pytest.mark.parametrize("field", ["config_fingerprint", "embedding_model", "embedding_revision"])
def test_generation_metadata_mismatch_prevents_preparing(tmp_path: Path, field: str) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        if field == "config_fingerprint":
            indexer = _indexer(tmp_path, repository)
            object.__setattr__(indexer._chunker, "_config_fingerprint", "a" * 64)
        elif field == "embedding_model":
            indexer = _indexer(
                tmp_path,
                repository,
                embeddings=FakeEmbeddingService(dimensions=3, model_identifier="wrong"),
            )
        else:
            indexer = _indexer(
                tmp_path,
                repository,
                embeddings=FakeEmbeddingService(dimensions=3, model_revision="wrong"),
            )
        with pytest.raises(RagOperationalError):
            indexer.prepare_document(generation.identity, version_id)
        with pytest.raises(RagOperationalError):
            repository.get_document_index(generation.identity, version_id)


def test_ready_requires_matching_verification(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        repository.begin_document_preparation(
            generation.identity, expected_revision=0, document_version_id=version_id
        )
        with pytest.raises(RagOperationalError) as caught:
            repository.finish_document_preparation(
                generation.identity,
                expected_revision=0,
                document_version_id=version_id,
                status=DocumentIndexStatus.READY,
                coverage_status=CoverageStatus.FULL,
                searchable_page_count=1,
                chunk_count=1,
            )
        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED


def test_post_preparing_failure_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        monkeypatch.setattr(
            repository,
            "insert_chunk",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("C:/secret/path")),
        )
        result = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert result.status is DocumentIndexStatus.FAILED
        assert "secret" not in str(result)
        with RagIndexLock(tmp_path / "phase4.lock").acquire():
            pass


@pytest.mark.parametrize(
    ("factory", "expected_code"),
    [
        (_ControlledDenseFactory(fail_upsert=True), RagErrorCode.DENSE_INDEX_WRITE_FAILED),
        (_ControlledDenseFactory(contains_result=False), RagErrorCode.INDEX_INTEGRITY_FAILED),
    ],
)
def test_dense_failure_never_publishes_ready_and_releases_lock(
    tmp_path: Path,
    factory: _ControlledDenseFactory,
    expected_code: RagErrorCode,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)

        result = _indexer(tmp_path, repository, dense=factory).prepare_document(
            generation.identity, version_id
        )

        assert (result.status, result.error_code) == (DocumentIndexStatus.FAILED, expected_code)
        assert repository.list_chunks(generation.identity, version_id) == ()
        with RagIndexLock(tmp_path / "phase4.lock").acquire():
            pass


def test_partial_dense_upsert_is_idempotently_replaced_on_retry(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        factory = _ControlledDenseFactory(fail_upsert=True)

        failed = _indexer(tmp_path, repository, dense=factory).prepare_document(
            generation.identity, version_id
        )
        assert failed.status is DocumentIndexStatus.FAILED
        assert factory.stored_count(generation.identity) == 1

        factory.fail_upsert = False
        ready = _indexer(tmp_path, repository, dense=factory).prepare_document(
            generation.identity, version_id
        )
        assert ready.status is DocumentIndexStatus.READY
        assert factory.stored_count(generation.identity) == ready.chunk_count
        assert len(repository.list_chunks(generation.identity, version_id)) == ready.chunk_count


def test_retry_from_preparing_and_ready_converges_without_duplicate_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        factory = _ControlledDenseFactory()
        original_finish = repository.finish_document_preparation

        def fail_final_state(*args: Any, **kwargs: Any) -> object:
            if kwargs.get("status") is DocumentIndexStatus.READY:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            if kwargs.get("status") is DocumentIndexStatus.FAILED:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            return original_finish(*args, **kwargs)

        monkeypatch.setattr(repository, "finish_document_preparation", fail_final_state)
        with pytest.raises(RagOperationalError):
            _indexer(tmp_path, repository, dense=factory).prepare_document(
                generation.identity, version_id
            )
        assert repository.get_document_index(generation.identity, version_id).status is (
            DocumentIndexStatus.PREPARING
        )

        monkeypatch.setattr(repository, "finish_document_preparation", original_finish)
        first = _indexer(tmp_path, repository, dense=factory).prepare_document(
            generation.identity, version_id
        )
        second = _indexer(tmp_path, repository, dense=factory).prepare_document(
            generation.identity, version_id
        )
        assert first.status is second.status is DocumentIndexStatus.READY
        assert first.verification is not None and second.verification is not None
        assert first.verification.chunk_keys == second.verification.chunk_keys
        assert len(repository.list_chunks(generation.identity, version_id)) == second.chunk_count
        assert factory.stored_count(generation.identity) == second.chunk_count


def test_final_ready_write_failure_records_safe_failed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        original_finish = repository.finish_document_preparation

        def fail_ready(*args: Any, **kwargs: Any) -> object:
            if kwargs.get("status") is DocumentIndexStatus.READY:
                raise RuntimeError("C:/private/ready failure")
            return original_finish(*args, **kwargs)

        monkeypatch.setattr(repository, "finish_document_preparation", fail_ready)
        result = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)

        assert (result.status, result.error_code) == (
            DocumentIndexStatus.FAILED,
            RagErrorCode.DOCUMENT_INDEX_FAILED,
        )
        assert "private" not in str(result)
        assert repository.list_chunks(generation.identity, version_id) == ()


def test_embedding_metadata_dimension_mismatch_prevents_preparing(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)

        with pytest.raises(RagOperationalError) as caught:
            _indexer(
                tmp_path,
                repository,
                embeddings=FakeEmbeddingService(dimensions=2),
            ).prepare_document(generation.identity, version_id)

        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        with pytest.raises(RagOperationalError):
            repository.get_document_index(generation.identity, version_id)


def test_ready_rejects_mismatched_verification_count(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        repository.begin_document_preparation(
            generation.identity, expected_revision=0, document_version_id=version_id
        )
        mismatched = DocumentVerificationRecord(
            generation.generation_id,
            version_id,
            ("fake-key",),
            2,
            1,
            1,
        )

        with pytest.raises(RagOperationalError) as caught:
            repository.finish_document_preparation(
                generation.identity,
                expected_revision=0,
                document_version_id=version_id,
                status=DocumentIndexStatus.READY,
                coverage_status=CoverageStatus.FULL,
                searchable_page_count=1,
                chunk_count=1,
                verification=mismatched,
            )

        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED


def test_verification_rejects_equal_count_wrong_fts_identity(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, engine):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        ready = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert ready.verification is not None
        chunk_id = repository.list_chunks(generation.identity, version_id)[0].chunk_id
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM rag_chunks_fts WHERE rowid = :rowid"), {"rowid": chunk_id}
            )
            connection.execute(
                text(
                    "INSERT INTO rag_chunks_fts(rowid, normalized_text, generation_id, "
                    "document_version_id) VALUES (:rowid, 'safe', :generation_id, :version_id)"
                ),
                {
                    "rowid": chunk_id + 10_000,
                    "generation_id": generation.generation_id,
                    "version_id": version_id,
                },
            )

        with pytest.raises(RagOperationalError) as caught:
            repository.verify_generation_document(generation.identity, version_id)
        assert caught.value.code is RagErrorCode.INDEX_INTEGRITY_FAILED


def test_verification_rejects_chunk_without_source_span(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, engine):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        ready = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert ready.verification is not None
        chunk_id = repository.list_chunks(generation.identity, version_id)[0].chunk_id
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM rag_chunk_source_spans WHERE rag_chunk_id = :chunk_id"),
                {"chunk_id": chunk_id},
            )

        with pytest.raises(RagOperationalError) as caught:
            repository.verify_generation_document(generation.identity, version_id)
        assert caught.value.code is RagErrorCode.INDEX_INTEGRITY_FAILED


def test_verification_rejects_an_invalid_extra_source_span(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, engine):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        ready = _indexer(tmp_path, repository).prepare_document(generation.identity, version_id)
        assert ready.verification is not None
        chunk_id = repository.list_chunks(generation.identity, version_id)[0].chunk_id
        with engine.begin() as connection:
            page_id = connection.scalar(
                select(DocumentPageORM.id).where(DocumentPageORM.document_version_id == version_id)
            )
            assert page_id is not None
            connection.execute(
                text(
                    "INSERT INTO rag_chunk_source_spans("
                    "rag_chunk_id, document_page_id, ordinal, page_number, "
                    "page_char_start, page_char_end, chunk_char_start, chunk_char_end"
                    ") VALUES (:chunk_id, :page_id, 2, 1, 0, 1, 1, 2)"
                ),
                {"chunk_id": chunk_id, "page_id": page_id},
            )

        with pytest.raises(RagOperationalError) as caught:
            repository.verify_generation_document(generation.identity, version_id)
        assert caught.value.code is RagErrorCode.INDEX_INTEGRITY_FAILED


@pytest.mark.parametrize(
    ("status", "searchable_pages", "error_code"),
    [
        (DocumentIndexStatus.FAILED, 0, None),
        (DocumentIndexStatus.NOT_SEARCHABLE, 1, None),
        (DocumentIndexStatus.NOT_SEARCHABLE, 0, RagErrorCode.DOCUMENT_INDEX_FAILED),
    ],
)
def test_terminal_state_rejects_incoherent_metadata(
    tmp_path: Path,
    status: DocumentIndexStatus,
    searchable_pages: int,
    error_code: RagErrorCode | None,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        repository.begin_document_preparation(
            generation.identity, expected_revision=0, document_version_id=version_id
        )

        with pytest.raises(RagOperationalError) as caught:
            repository.finish_document_preparation(
                generation.identity,
                expected_revision=0,
                document_version_id=version_id,
                status=status,
                coverage_status=CoverageStatus.NONE,
                searchable_page_count=searchable_pages,
                chunk_count=0,
                error_code=error_code,
            )

        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
