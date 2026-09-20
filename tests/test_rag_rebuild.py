"""Blue/green rebuild, integrity, and recovery tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
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
from aaos.rag.chunking import DeterministicChunker
from aaos.rag.config import RagIndexConfig, compute_config_fingerprint
from aaos.rag.contracts import (
    DocumentIndexStatus,
    GenerationStatus,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)
from aaos.rag.dense import InMemoryDenseIndexFactory
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.indexing import RagDocumentIndexer, RagIndexingCoordinator
from aaos.rag.lock import RagIndexLock
from aaos.rag.repository import GenerationBuild, GenerationIntegrityReport, RagRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def _database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Engine]]:
    database_path = tmp_path / "rag-rebuild.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, engine
    finally:
        engine.dispose()


def _seed(sessions: SessionFactory, page_texts: tuple[str, ...]) -> int:
    digest = sha256("\n".join(page_texts).encode("utf-8")).hexdigest()
    with sessions() as session, session.begin():
        course = CourseORM(source_system="test", source_id=digest[:12], title="Course")
        session.add(course)
        session.flush()
        remote = RemoteFileORM(
            course_id=course.id,
            source_system="test",
            source_id=f"remote-{digest[:12]}",
            title="File",
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
                page_number=number,
                text=text,
                text_source="EMBEDDED_PDF",
                width_points=612.0,
                height_points=792.0,
                created_at=NOW,
            )
            for number, text in enumerate(page_texts, start=1)
        )
        return int(version.id)


def _config_value() -> RagIndexConfig:
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


def _generation(repository: RagRepository) -> GenerationBuild:
    config = _config_value()
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


def _indexer(
    tmp_path: Path,
    repository: RagRepository,
    dense: InMemoryDenseIndexFactory,
    *,
    embeddings: FakeEmbeddingService | None = None,
) -> RagDocumentIndexer:
    return RagDocumentIndexer(
        repository,
        DeterministicChunker(_config_value()),
        embeddings or FakeEmbeddingService(dimensions=3),
        dense,
        RagIndexLock(tmp_path / "phase4.lock"),
    )


def _active_setup(
    tmp_path: Path, repository: RagRepository, sessions: SessionFactory
) -> tuple[GenerationBuild, int, InMemoryDenseIndexFactory]:
    version_id = _seed(sessions, ("A searchable page with enough text.",))
    dense = InMemoryDenseIndexFactory()
    indexer = _indexer(tmp_path, repository, dense)
    generation = _generation(repository)
    prepared = indexer.prepare_document(generation.identity, version_id)
    assert prepared.status is DocumentIndexStatus.READY
    active = repository.activate_generation(generation.identity, expected_revision=0)
    return active, version_id, dense


def _coordinator(
    tmp_path: Path,
    repository: RagRepository,
    dense: InMemoryDenseIndexFactory,
    indexer: RagDocumentIndexer,
) -> RagIndexingCoordinator:
    return RagIndexingCoordinator(repository, indexer, RagIndexLock(tmp_path / "phase4.lock"))


class _FailOnCallEmbeddings(FakeEmbeddingService):
    def __init__(self, failing_call: int) -> None:
        super().__init__(dimensions=3)
        self._failing_call = failing_call
        self._calls = 0

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self._calls += 1
        if self._calls == self._failing_call:
            raise RuntimeError()
        return super().embed_documents(texts)


def test_rebuild_creates_isolated_building_generation_and_atomically_activates(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        old_active, version_id, dense = _active_setup(tmp_path, repository, sessions)
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )

        result = coordinator.rebuild()

        assert result.safe_status == "PASS"
        assert result.generation_id != old_active.id
        new_active = repository.get_active_generation()
        assert new_active.id == result.generation_id
        assert new_active.status is GenerationStatus.ACTIVE
        assert repository.get_generation(old_active.id).status is GenerationStatus.RETIRED
        assert (
            repository.get_document_index(new_active.identity, version_id).status
            is DocumentIndexStatus.READY
        )
        assert repository.list_chunks(old_active.identity, version_id)
        assert repository.list_chunks(new_active.identity, version_id)
        old_dense = dense.open(old_active.identity)
        new_dense = dense.open(new_active.identity)
        try:
            assert old_dense.count() > 0
            assert new_dense.count() == old_dense.count()
        finally:
            old_dense.close()
            new_dense.close()


def test_rebuild_publishes_ready_partial_and_not_searchable_documents(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        searchable_id = _seed(sessions, ("A searchable page with enough text.",))
        partial_id = _seed(sessions, ("A searchable page.", "   "))
        empty_id = _seed(sessions, ("   ",))
        dense = InMemoryDenseIndexFactory()
        indexer = _indexer(tmp_path, repository, dense)
        generation = _generation(repository)
        for version_id in (searchable_id, partial_id, empty_id):
            prepared = indexer.prepare_document(generation.identity, version_id)
            assert prepared.status in {
                DocumentIndexStatus.READY,
                DocumentIndexStatus.NOT_SEARCHABLE,
            }
        active = repository.activate_generation(generation.identity, expected_revision=0)
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )

        result = coordinator.rebuild()

        assert result.generation_id != active.id
        assert result.indexed_documents == 2
        assert result.not_searchable_documents == 1
        rebuilt = repository.get_generation(result.generation_id)
        statuses = {
            version_id: repository.get_document_index(rebuilt.identity, version_id).status
            for version_id in (searchable_id, partial_id, empty_id)
        }
        assert statuses == {
            searchable_id: DocumentIndexStatus.READY,
            partial_id: DocumentIndexStatus.READY,
            empty_id: DocumentIndexStatus.NOT_SEARCHABLE,
        }


def test_rebuild_retry_reuses_completed_documents_without_duplicate_derived_rows(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        first_id = _seed(sessions, ("first searchable text",))
        second_id = _seed(sessions, ("second searchable text",))
        dense = InMemoryDenseIndexFactory()
        initial_indexer = _indexer(tmp_path, repository, dense)
        generation = _generation(repository)
        for version_id in (first_id, second_id):
            assert (
                initial_indexer.prepare_document(generation.identity, version_id).status
                is DocumentIndexStatus.READY
            )
        old_active = repository.activate_generation(generation.identity, expected_revision=0)

        failing_indexer = _indexer(
            tmp_path,
            repository,
            dense,
            embeddings=_FailOnCallEmbeddings(failing_call=2),
        )
        failing = _coordinator(tmp_path, repository, dense, failing_indexer)
        with pytest.raises(RagOperationalError) as raised:
            failing.rebuild()
        assert raised.value.code is RagErrorCode.EMBEDDING_FAILED
        building = repository.list_building_generations()
        assert len(building) == 1
        first_chunks_before_retry = repository.list_chunks(building[0].identity, first_id)

        healthy = _coordinator(tmp_path, repository, dense, _indexer(tmp_path, repository, dense))
        result = healthy.rebuild()

        assert result.generation_id == building[0].generation_id
        assert repository.get_active_generation().id == result.generation_id
        assert repository.get_generation(old_active.id).status is GenerationStatus.RETIRED
        assert repository.list_chunks(building[0].identity, first_id) == first_chunks_before_retry
        assert len(repository.list_chunks(building[0].identity, second_id)) > 0
        rebuilt_dense = dense.open(building[0].identity)
        try:
            assert rebuilt_dense.count() == len(
                repository.list_chunks(building[0].identity, first_id)
                + repository.list_chunks(building[0].identity, second_id)
            )
        finally:
            rebuilt_dense.close()


def test_rebuild_failure_preserves_active_and_leaves_building_non_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        old_active, _, dense = _active_setup(tmp_path, repository, sessions)
        failing = FakeEmbeddingService(dimensions=3)
        indexer = _indexer(tmp_path, repository, dense, embeddings=failing)
        coordinator = _coordinator(tmp_path, repository, dense, indexer)

        def fail(_texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            raise RuntimeError()

        monkeypatch.setattr(indexer._embedding_service, "embed_documents", fail)
        with pytest.raises(RagOperationalError) as raised:
            coordinator.rebuild()

        assert raised.value.code is RagErrorCode.EMBEDDING_FAILED
        assert repository.get_active_generation().id == old_active.id
        building = repository.list_building_generations()
        assert len(building) == 1
        assert building[0].status is GenerationStatus.BUILDING


def test_rebuild_integrity_report_requires_all_expected_documents(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        active, version_id, dense = _active_setup(tmp_path, repository, sessions)
        indexer = _indexer(tmp_path, repository, dense)
        coordinator = _coordinator(tmp_path, repository, dense, indexer)

        result = coordinator.rebuild()
        report = coordinator.verify_generation(
            repository.get_generation(result.generation_id).identity,
            (version_id,),
        )

        assert report.valid is True
        assert report.expected_document_count == 1
        assert report.ready_document_count == 1
        assert report.not_searchable_document_count == 0


def test_integrity_failure_preserves_active_and_leaves_building_non_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        old_active, _, dense = _active_setup(tmp_path, repository, sessions)
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )
        original = coordinator.verify_generation

        def report_invalid(
            identity: RagGenerationIdentity,
            expected: tuple[int, ...] | None = None,
        ) -> GenerationIntegrityReport:
            report = original(identity, expected)
            return replace(report, valid=False, dense_index_healthy=False)

        monkeypatch.setattr(coordinator, "verify_generation", report_invalid)
        with pytest.raises(RagOperationalError) as raised:
            coordinator.rebuild()

        assert raised.value.code is RagErrorCode.INDEX_INTEGRITY_FAILED
        assert repository.get_active_generation().id == old_active.id
        assert len(repository.list_building_generations()) == 1


def test_repository_integrity_report_detects_fts_mismatch(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        active, version_id, _ = _active_setup(tmp_path, repository, sessions)
        with sessions() as session, session.begin():
            session.execute(
                text(
                    "DELETE FROM rag_chunks_fts WHERE rowid = "
                    "(SELECT id FROM rag_chunks WHERE generation_id = :generation_id LIMIT 1)"
                ),
                {"generation_id": active.id},
            )

        report = repository.verify_generation(active.identity, (version_id,))

        assert report.valid is False
        assert report.chunk_count != report.fts_row_count


def test_activation_rejects_unexpected_active_generation_without_retiring_it(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        active, _, _ = _active_setup(tmp_path, repository, sessions)
        candidate = _generation(repository)

        with pytest.raises(RagOperationalError) as raised:
            repository.activate_generation(
                candidate.identity,
                expected_revision=0,
                expected_active_generation_id=active.id + 1,
            )

        assert raised.value.code is RagErrorCode.GENERATION_ACTIVATION_FAILED
        assert repository.get_active_generation().id == active.id
        assert repository.get_generation(candidate.id).status is GenerationStatus.BUILDING


def test_rebuild_recovery_marks_interrupted_preparing_generation_failed(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        active, version_id, dense = _active_setup(tmp_path, repository, sessions)
        building = _generation(repository)
        repository.begin_document_preparation(
            building.identity,
            expected_revision=building.identity.content_revision,
            document_version_id=version_id,
        )
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )

        recovered = coordinator.recover_building_generations()

        assert building.id in recovered
        assert repository.get_active_generation().id == active.id
        assert (
            repository.get_document_index(building.identity, version_id).status
            is DocumentIndexStatus.FAILED
        )


def test_status_reports_only_active_generation_and_cross_store_counts(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        active, _, dense = _active_setup(tmp_path, repository, sessions)
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )

        status = coordinator.status()

        assert status.active_generation == active.identity
        assert status.ready_documents == 1
        assert status.partial_documents == 0
        assert status.not_searchable_documents == 0
        assert status.chunks > 0
        assert status.dense_index_healthy is True


def test_prepare_document_accepts_existing_build_lease_without_nested_acquisition(
    tmp_path: Path,
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("A searchable page with enough text.",))
        dense = InMemoryDenseIndexFactory()
        indexer = _indexer(tmp_path, repository, dense)
        building = _generation(repository)
        lock = RagIndexLock(tmp_path / "phase4.lock")
        with lock.acquire() as lease:
            prepared = indexer.prepare_document(building.identity, version_id, lease=lease)

        assert prepared.status is DocumentIndexStatus.READY


def test_rebuild_never_uses_active_generation_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        old_active, _, dense = _active_setup(tmp_path, repository, sessions)
        indexer = _indexer(tmp_path, repository, dense)
        coordinator = _coordinator(tmp_path, repository, dense, indexer)
        calls: list[str] = []
        original = indexer.stage_document_for_publication

        def forbidden(*args: object, **kwargs: object) -> object:
            calls.append("stage")
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(indexer, "stage_document_for_publication", forbidden)
        result = coordinator.rebuild()

        assert result.generation_id != old_active.id
        assert calls == []


def test_rebuild_result_is_safe_for_empty_current_corpus(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, _, _):
        dense = InMemoryDenseIndexFactory()
        coordinator = _coordinator(
            tmp_path, repository, dense, _indexer(tmp_path, repository, dense)
        )

        with pytest.raises(RagOperationalError) as raised:
            coordinator.rebuild()

        assert raised.value.code in {
            RagErrorCode.NO_ACTIVE_GENERATION,
            RagErrorCode.NO_ELIGIBLE_DOCUMENTS,
        }


def test_rebuild_bootstraps_first_active_generation(tmp_path: Path) -> None:
    with _database(tmp_path) as (repository, sessions, _):
        version_id = _seed(sessions, ("first generation searchable text",))
        dense = InMemoryDenseIndexFactory()
        indexer = _indexer(tmp_path, repository, dense)
        coordinator = _coordinator(tmp_path, repository, dense, indexer)

        result = coordinator.rebuild()

        assert result.safe_status == "PASS"
        active = repository.get_active_generation()
        assert active.generation_id == result.generation_id
        assert active.status is GenerationStatus.ACTIVE
        assert (
            repository.get_document_index(active.identity, version_id).status
            is DocumentIndexStatus.READY
        )
        assert repository.list_chunks(active.identity, version_id)
