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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentStructureUnitORM,
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
    GlobalScope,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)
from aaos.rag.dense import InMemoryDenseIndexFactory
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.indexing import RagDocumentIndexer, RagIndexingCoordinator
from aaos.rag.lock import RagIndexLock
from aaos.rag.repository import (
    DocumentVerificationRecord,
    GenerationBuild,
    RagRepository,
    StagedDocumentIndexResult,
)

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


def _seed_next_version(
    sessions: SessionFactory, current_version_id: int, page_texts: tuple[str, ...]
) -> int:
    with sessions() as session, session.begin():
        current = session.get(DocumentVersionORM, current_version_id)
        assert current is not None
        digest = sha256(("next\n" + "\n".join(page_texts)).encode("utf-8")).hexdigest()
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
            document_id=current.document_id,
            version_number=current.version_number + 1,
            content_blob_id=blob.id,
            source_filename="file-v2.pdf",
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
                text=page_text,
                text_source="EMBEDDED_PDF",
                width_points=612.0,
                height_points=792.0,
                created_at=NOW,
            )
            for index, page_text in enumerate(page_texts, start=1)
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


def test_stage_document_uses_owned_lease_and_leaves_searchable_preparing(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        lease = lock.acquire()
        try:
            staged = indexer.stage_document_for_publication(lease, active.identity, version_id)
            assert staged.intended_status is DocumentIndexStatus.READY
            assert staged.verification is not None
            assert repository.get_document_index(active.identity, version_id).status is (
                DocumentIndexStatus.PREPARING
            )
            lock.assert_owned(lease)
        finally:
            lease.release()


def test_stage_document_leaves_empty_document_preparing_for_not_searchable(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("   ",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        with lock.acquire() as lease:
            staged = indexer.stage_document_for_publication(lease, active.identity, version_id)
        assert staged.intended_status is DocumentIndexStatus.NOT_SEARCHABLE
        assert staged.verification is None
        assert repository.get_document_index(active.identity, version_id).status is (
            DocumentIndexStatus.PREPARING
        )


def test_staged_result_rejects_non_publishable_terminal_status() -> None:
    identity = RagGenerationIdentity(1, "a" * 64, 0, 3)

    with pytest.raises(ValueError, match="intended_status"):
        StagedDocumentIndexResult(
            identity,
            1,
            DocumentIndexStatus.FAILED,
            CoverageStatus.NONE,
            0,
            0,
            None,
        )


def test_index_new_publishes_two_documents_with_one_revision_increment(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        first_id = _seed(sessions, ("first searchable text",))
        second_id = _seed(sessions, ("second searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )

        result = RagIndexingCoordinator(repository, indexer, lock).index_new()

        assert result.content_revision == 1
        assert result.indexed_documents == 2
        refreshed = repository.get_generation(active.generation_id)
        assert refreshed.identity.content_revision == 1
        assert {
            item.document_version_id: item.status
            for item in repository.list_document_indexes(refreshed.identity)
        } == {
            first_id: DocumentIndexStatus.READY,
            second_id: DocumentIndexStatus.READY,
        }


def test_index_new_stages_in_stable_order_with_the_same_outer_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_ids = tuple(
            _seed(sessions, (f"searchable document {ordinal}",)) for ordinal in (3, 1, 2)
        )
        generation = _generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        original = indexer.stage_document_for_publication
        calls: list[tuple[int, int]] = []

        def record_call(lease: object, identity: RagGenerationIdentity, version_id: int) -> object:
            calls.append((id(lease), version_id))
            return original(lease, identity, version_id)  # type: ignore[arg-type]

        monkeypatch.setattr(indexer, "stage_document_for_publication", record_call)
        RagIndexingCoordinator(repository, indexer, lock).index_new()

        assert tuple(version_id for _, version_id in calls) == tuple(sorted(version_ids))
        assert len({lease_id for lease_id, _ in calls}) == 1


@pytest.mark.parametrize("document_version_id", (0, True, 999_999))
def test_index_new_rejects_invalid_or_ineligible_explicit_id(
    tmp_path: Path, document_version_id: int
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        coordinator = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ),
            lock,
        )

        with pytest.raises(RagOperationalError) as caught:
            coordinator.index_new(document_version_id)

        assert caught.value.code is RagErrorCode.NO_ELIGIBLE_DOCUMENTS
        assert repository.get_generation(active.generation_id).identity.content_revision == 0


def test_index_new_retries_failed_active_document_and_publishes_partial_coverage(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text", "   "))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        repository.begin_document_staging(
            active.identity, expected_revision=0, document_version_id=version_id
        )
        repository.finish_document_preparation(
            active.identity,
            expected_revision=0,
            document_version_id=version_id,
            status=DocumentIndexStatus.FAILED,
            coverage_status=CoverageStatus.NONE,
            searchable_page_count=0,
            chunk_count=0,
            error_code=RagErrorCode.DOCUMENT_INDEX_FAILED,
        )
        lock = RagIndexLock(tmp_path / "phase4.lock")
        result = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ),
            lock,
        ).index_new()

        identity = repository.get_generation(active.generation_id).identity
        stored = repository.get_document_index(identity, version_id)
        assert result.content_revision == 1
        assert (stored.status, stored.coverage_status) == (
            DocumentIndexStatus.READY,
            CoverageStatus.PARTIAL,
        )


@pytest.mark.parametrize("failure_position", (0, 2))
def test_index_new_first_or_final_staging_failure_never_publishes_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_position: int
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_ids = tuple(_seed(sessions, (f"searchable {ordinal}",)) for ordinal in range(3))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        original = indexer.stage_document_for_publication

        def fail_selected(
            lease: object, identity: RagGenerationIdentity, version_id: int
        ) -> object:
            if version_id == version_ids[failure_position]:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            return original(lease, identity, version_id)  # type: ignore[arg-type]

        monkeypatch.setattr(indexer, "stage_document_for_publication", fail_selected)
        with pytest.raises(RagOperationalError):
            RagIndexingCoordinator(repository, indexer, lock).index_new()

        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        assert all(
            item.status is DocumentIndexStatus.PREPARING
            for item in repository.list_document_indexes(active.identity)
        )
        with lock.acquire():
            pass


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


class _CountingEmbeddings(FakeEmbeddingService):
    def __init__(self) -> None:
        super().__init__(dimensions=3)
        self.release_calls = 0

    def release_model(self) -> None:
        self.release_calls += 1
        super().release_model()


class _ReleaseFailureEmbeddings(_CountingEmbeddings):
    def release_model(self) -> None:
        self.release_calls += 1
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


class _LeaseInvalidatingDenseFactory:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._inner = InMemoryDenseIndexFactory()
        self.upsert_calls = 0

    def open(self, generation: RagGenerationIdentity) -> DenseIndex:
        nonce = self._lock_path.read_bytes()
        self._lock_path.unlink()
        self._lock_path.write_bytes(nonce)
        inner = self._inner.open(generation)
        owner = self

        class _Index:
            def upsert(self, records: tuple[DenseVectorRecord, ...]) -> None:
                owner.upsert_calls += 1
                inner.upsert(records)

            def search(
                self,
                vector: tuple[float, ...],
                *,
                limit: int,
                allowed_document_version_ids: tuple[int, ...],
            ) -> tuple[DenseSearchHit, ...]:
                return inner.search(
                    vector,
                    limit=limit,
                    allowed_document_version_ids=allowed_document_version_ids,
                )

            def contains(self, chunk_keys: tuple[str, ...]) -> bool:
                return inner.contains(chunk_keys)

            def count(self) -> int:
                return inner.count()

            def close(self) -> None:
                inner.close()

        return _Index()

    def remove(self, generation: RagGenerationIdentity) -> None:
        self._inner.remove(generation)


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


def test_index_new_no_op_skips_terminal_documents_and_revision_churn(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        embeddings = _CountingEmbeddings()
        indexer = RagDocumentIndexer(
            repository, _chunker(), embeddings, InMemoryDenseIndexFactory(), lock
        )
        coordinator = RagIndexingCoordinator(repository, indexer, lock)
        first = coordinator.index_new(version_id)
        second = coordinator.index_new(version_id)

        assert (first.content_revision, second.safe_status, second.content_revision) == (
            1,
            "NO_OP",
            1,
        )
        assert embeddings.release_calls == 1
        assert repository.get_generation(active.generation_id).identity.content_revision == 1


def test_index_new_empty_corpus_is_no_op_without_embedding_lifecycle(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, _, _engine):
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        embeddings = _CountingEmbeddings()
        result = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository, _chunker(), embeddings, InMemoryDenseIndexFactory(), lock
            ),
            lock,
        ).index_new()

        assert (result.safe_status, result.content_revision) == ("NO_OP", 0)
        assert embeddings.release_calls == 0
        assert repository.get_generation(active.generation_id).identity.content_revision == 0


def test_record_staged_document_rejects_boolean_counts(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        repository.begin_document_staging(
            active.identity, expected_revision=0, document_version_id=version_id
        )

        with pytest.raises(RagOperationalError):
            repository.record_staged_document(
                active.identity,
                expected_revision=0,
                document_version_id=version_id,
                intended_status=DocumentIndexStatus.READY,
                coverage_status=CoverageStatus.FULL,
                searchable_page_count=True,
                chunk_count=True,
                verification=None,
            )


def test_index_new_not_searchable_is_published_once_as_corpus_change(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("   ",))
        generation = _generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        coordinator = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ),
            lock,
        )

        result = coordinator.index_new()

        refreshed = repository.get_generation(generation.generation_id)
        assert (result.content_revision, result.not_searchable_documents) == (1, 1)
        assert repository.get_document_index(refreshed.identity, version_id).status is (
            DocumentIndexStatus.NOT_SEARCHABLE
        )
        assert coordinator.index_new().safe_status == "NO_OP"


def test_index_new_staging_failure_keeps_sibling_preparing_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        first_id = _seed(sessions, ("first searchable text",))
        second_id = _seed(sessions, ("second searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        embeddings = _CountingEmbeddings()
        indexer = RagDocumentIndexer(
            repository, _chunker(), embeddings, InMemoryDenseIndexFactory(), lock
        )
        original = indexer.stage_document_for_publication

        def fail_second(lease: object, identity: RagGenerationIdentity, version_id: int) -> object:
            if version_id == second_id:
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            return original(lease, identity, version_id)  # type: ignore[arg-type]

        monkeypatch.setattr(indexer, "stage_document_for_publication", fail_second)
        with pytest.raises(RagOperationalError):
            RagIndexingCoordinator(repository, indexer, lock).index_new()

        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        assert repository.get_document_index(active.identity, first_id).status is (
            DocumentIndexStatus.PREPARING
        )
        with pytest.raises(RagOperationalError):
            repository.get_document_index(active.identity, second_id)
        assert embeddings.release_calls == 1
        with lock.acquire():
            pass

        monkeypatch.setattr(indexer, "stage_document_for_publication", original)
        retried = RagIndexingCoordinator(repository, indexer, lock).index_new()
        assert retried.content_revision == 1
        retried_identity = repository.get_generation(active.generation_id).identity
        chunk_keys = tuple(chunk.chunk_key for chunk in repository.list_chunks(retried_identity))
        assert len(chunk_keys) == len(set(chunk_keys))
        assert retried_identity.content_revision == 1


def test_index_new_release_failure_prevents_publication_and_releases_lease(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        embeddings = _ReleaseFailureEmbeddings()
        coordinator = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository, _chunker(), embeddings, InMemoryDenseIndexFactory(), lock
            ),
            lock,
        )

        with pytest.raises(RagOperationalError):
            coordinator.index_new()

        assert embeddings.release_calls == 1
        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        assert repository.get_document_index(active.identity, version_id).status is (
            DocumentIndexStatus.PREPARING
        )
        with lock.acquire():
            pass


def test_index_new_rejects_historical_explicit_version_before_staging(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        historical_id = _seed(sessions, ("version one",))
        current_id = _seed_next_version(sessions, historical_id, ("version two",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        coordinator = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ),
            lock,
        )

        with pytest.raises(RagOperationalError) as caught:
            coordinator.index_new(historical_id)

        assert caught.value.code is RagErrorCode.NO_ELIGIBLE_DOCUMENTS
        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        with pytest.raises(RagOperationalError):
            repository.get_document_index(active.identity, current_id)


def test_new_not_searchable_version_supersedes_old_ready_without_deleting_history(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        first_id = _seed(sessions, ("version one searchable",))
        generation = _generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        coordinator = RagIndexingCoordinator(
            repository,
            RagDocumentIndexer(
                repository,
                _chunker(),
                FakeEmbeddingService(dimensions=3),
                InMemoryDenseIndexFactory(),
                lock,
            ),
            lock,
        )
        coordinator.index_new()
        second_id = _seed_next_version(sessions, first_id, ("   ",))

        result = coordinator.index_new()

        identity = repository.get_generation(generation.generation_id).identity
        states = {
            item.document_version_id: item.status
            for item in repository.list_document_indexes(identity)
        }
        assert result.content_revision == 2
        assert states == {
            first_id: DocumentIndexStatus.READY,
            second_id: DocumentIndexStatus.NOT_SEARCHABLE,
        }
        current = repository.resolve_current_versions(GlobalScope())
        assert tuple(item.document_version_id for item in current) == (second_id,)
        searchable_current_ids = tuple(
            item.document_version_id
            for item in current
            if repository.get_document_index(identity, item.document_version_id).status
            is DocumentIndexStatus.READY
        )
        assert searchable_current_ids == ()
        assert repository.resolve_index_new_candidates(identity) == ()


def test_publication_cas_failure_rolls_back_all_terminal_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        first_id = _seed(sessions, ("first searchable text",))
        second_id = _seed(sessions, ("second searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        with lock.acquire() as lease:
            staged = tuple(
                indexer.stage_document_for_publication(lease, active.identity, version_id)
                for version_id in (first_id, second_id)
            )
            monkeypatch.setattr(
                repository,
                "_bump_content_revision_in_session",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                ),
            )
            with pytest.raises(RagOperationalError):
                repository.publish_staged_documents(
                    active.identity, expected_revision=0, staged_documents=staged
                )

        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        assert {
            item.document_version_id: item.status
            for item in repository.list_document_indexes(active.identity)
        } == {
            first_id: DocumentIndexStatus.PREPARING,
            second_id: DocumentIndexStatus.PREPARING,
        }


def test_publication_database_failure_rolls_back_terminal_states_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            InMemoryDenseIndexFactory(),
            lock,
        )
        with lock.acquire() as lease:
            staged = indexer.stage_document_for_publication(lease, active.identity, version_id)
            monkeypatch.setattr(
                repository,
                "_bump_content_revision_in_session",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    SQLAlchemyError("private persistence failure")
                ),
            )
            with pytest.raises(RagOperationalError) as caught:
                repository.publish_staged_documents(
                    active.identity, expected_revision=0, staged_documents=(staged,)
                )

        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        assert "private" not in str(caught.value)
        assert repository.get_generation(active.generation_id).identity.content_revision == 0
        assert repository.get_document_index(active.identity, version_id).status is (
            DocumentIndexStatus.PREPARING
        )


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


def test_stage_revalidates_lease_after_dense_open_before_upsert(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("searchable text",))
        generation = _generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        lock_path = tmp_path / "phase4.lock"
        lock = RagIndexLock(lock_path)
        factory = _LeaseInvalidatingDenseFactory(lock_path)
        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            FakeEmbeddingService(dimensions=3),
            factory,
            lock,
        )
        lease = lock.acquire()

        try:
            with pytest.raises(RagOperationalError):
                indexer.stage_document_for_publication(lease, active.identity, version_id)
            assert factory.upsert_calls == 0
        finally:
            lock_path.unlink(missing_ok=True)


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

def _convert_to_structural_index_fixture(
    sessions: SessionFactory,
    document_version_id: int,
) -> tuple[int, int]:
    # Convert one seeded PAGE fixture into a valid STRUCTURE document.
    with sessions() as session, session.begin():
        version = session.get(
            DocumentVersionORM,
            document_version_id,
        )
        assert version is not None

        session.query(DocumentPageORM).filter(
            DocumentPageORM.document_version_id
            == document_version_id
        ).delete(synchronize_session=False)

        version.source_filename = "structural-notes.docx"
        version.provenance_mode = "STRUCTURE"
        version.page_count = None

        heading = DocumentStructureUnitORM(
            document_version_id=document_version_id,
            ordinal=1,
            kind="HEADING",
            text="Neural Networks",
            heading_level=1,
            table_index=None,
            row_index=None,
            column_index=None,
            created_at=NOW,
        )

        paragraph = DocumentStructureUnitORM(
            document_version_id=document_version_id,
            ordinal=2,
            kind="PARAGRAPH",
            text="A neural network learns patterns.",
            heading_level=None,
            table_index=None,
            row_index=None,
            column_index=None,
            created_at=NOW,
        )

        session.add_all(
            (
                heading,
                paragraph,
            )
        )
        session.flush()

        return (
            int(heading.id),
            int(paragraph.id),
        )


def test_prepare_document_indexes_structure_provenance_without_fake_pages(
    tmp_path: Path,
) -> None:
    with _database(
        tmp_path
    ) as (repository, sessions, _):
        version_id = _seed(
            sessions,
            ("temporary PAGE fixture",),
        )

        expected_unit_ids = (
            _convert_to_structural_index_fixture(
                sessions,
                version_id,
            )
        )

        generation = _generation(repository)

        version = repository.get_eligible_document_version(
            version_id
        )
        assert version.provenance_mode == "STRUCTURE"
        assert version.page_count is None

        structure_units = repository.read_structure_units(
            version_id
        )
        assert len(structure_units) == 2
        assert tuple(
            unit.text for unit in structure_units
        ) == (
            "Neural Networks",
            "A neural network learns patterns.",
        )

        result = _indexer(
            tmp_path,
            repository,
        ).prepare_document(
            generation.identity,
            version_id,
        )

        assert result.status is DocumentIndexStatus.READY
        assert result.coverage_status is CoverageStatus.FULL

        # STRUCTURE documents must never invent searchable pages.
        assert result.searchable_page_count == 0
        assert result.chunk_count > 0
        assert result.verification is not None

        chunks = repository.list_chunks(
            generation.identity,
            version_id,
        )

        assert chunks
        assert all(
            chunk.provenance_mode == "STRUCTURE"
            for chunk in chunks
        )
        assert all(
            chunk.page_start is None
            and chunk.page_end is None
            for chunk in chunks
        )

        verification = (
            repository.verify_generation_document(
                generation.identity,
                version_id,
            )
        )

        assert verification == result.verification
        assert verification.source_span_count == len(
            expected_unit_ids
        )

        with sessions() as session:
            assert (
                session.query(DocumentPageORM)
                .filter(
                    DocumentPageORM.document_version_id
                    == version_id
                )
                .count()
                == 0
            )

        indexed = repository.get_document_index(
            generation.identity,
            version_id,
        )

        assert indexed.status is DocumentIndexStatus.READY
        assert indexed.searchable_page_count == 0

def test_index_new_structural_document_publishes_once_then_no_ops(
    tmp_path: Path,
) -> None:
    with _database(
        tmp_path
    ) as (repository, sessions, _):
        version_id = _seed(
            sessions,
            ("temporary PAGE fixture",),
        )

        _convert_to_structural_index_fixture(
            sessions,
            version_id,
        )

        generation = _generation(repository)
        active = repository.activate_generation(
            generation.identity,
            expected_revision=0,
        )

        lock = RagIndexLock(
            tmp_path / "phase4-structural-index-new.lock"
        )
        embeddings = _CountingEmbeddings()

        indexer = RagDocumentIndexer(
            repository,
            _chunker(),
            embeddings,
            InMemoryDenseIndexFactory(),
            lock,
        )
        coordinator = RagIndexingCoordinator(
            repository,
            indexer,
            lock,
        )

        before = repository.get_generation(
            active.generation_id
        ).identity

        assert before.content_revision == 0

        first = coordinator.index_new(
            version_id
        )

        after_first = repository.get_generation(
            active.generation_id
        ).identity

        chunks = repository.list_chunks(
            after_first,
            version_id,
        )

        assert first.content_revision == 1
        assert after_first.content_revision == 1

        assert chunks
        assert all(
            chunk.provenance_mode == "STRUCTURE"
            for chunk in chunks
        )
        assert all(
            chunk.page_start is None
            and chunk.page_end is None
            for chunk in chunks
        )

        indexed = repository.get_document_index(
            after_first,
            version_id,
        )

        assert indexed.status is DocumentIndexStatus.READY
        assert indexed.coverage_status is CoverageStatus.FULL
        assert indexed.searchable_page_count == 0
        assert indexed.chunk_count == len(chunks)

        second = coordinator.index_new(
            version_id
        )

        after_second = repository.get_generation(
            active.generation_id
        ).identity

        assert second.safe_status == "NO_OP"
        assert second.content_revision == 1
        assert after_second.content_revision == 1

        # The unchanged second run must not reload/release
        # the embedding model.
        assert embeddings.release_calls == 1

        second_chunks = repository.list_chunks(
            after_second,
            version_id,
        )

        assert tuple(
            chunk.chunk_key
            for chunk in second_chunks
        ) == tuple(
            chunk.chunk_key
            for chunk in chunks
        )
