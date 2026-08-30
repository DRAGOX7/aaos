"""Task 14 query-scope and immutable snapshot tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentVersionORM,
)
from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    CourseScope,
    CoverageStatus,
    DenseSearchHit,
    DocumentIndexStatus,
    EmbeddingModelMetadata,
    GlobalScope,
    HistoricalScope,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
)
from aaos.rag.repository import GenerationBuild, RagRepository
from aaos.rag.retrieval import (
    HybridRetriever,
    QuerySnapshotResolver,
    RetrievalCandidateSet,
    normalize_query,
    resolve_query_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def repository_database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Engine]]:
    database_path = tmp_path / "rag-retrieval.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, engine
    finally:
        engine.dispose()


def _seed_documents(sessions: SessionFactory, *, course_count: int = 2) -> dict[str, object]:
    with sessions() as session, session.begin():
        courses: list[CourseORM] = []
        for index in range(course_count):
            course = CourseORM(
                source_system="phase3",
                source_id=f"course-{index}",
                title=f"Course {index}",
                is_active=True,
            )
            session.add(course)
            session.flush()
            courses.append(course)

        remotes: list[RemoteFileORM] = []
        for index, course in enumerate(courses):
            remote = RemoteFileORM(
                course_id=course.id,
                source_system="phase3",
                source_id=f"remote-{index}",
                title=f"Remote {index}",
                is_active=True,
            )
            session.add(remote)
            session.flush()
            remotes.append(remote)

        versions: dict[str, int] = {}
        for index, remote in enumerate(remotes):
            document = DocumentORM(
                remote_file_id=remote.id,
                ingestion_status=IngestionStatus.READY.value,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(document)
            session.flush()
            for version_number in (1, 2):
                digest = (f"{index + 1:08x}{version_number:08x}").ljust(64, "a")
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
                    version_number=version_number,
                    content_blob_id=blob.id,
                    source_filename=f"course-{index}-{version_number}.pdf",
                    ingested_at=NOW,
                    extraction_status="SUCCESS",
                    page_count=1,
                )
                session.add(version)
                session.flush()
                versions[f"course{index}_v{version_number}"] = int(version.id)
        return {"courses": tuple(int(course.id) for course in courses), "versions": versions}


def _create_generation(
    repository: RagRepository, *, fingerprint: str = "a" * 64
) -> GenerationBuild:
    return repository.create_generation(
        config_fingerprint=fingerprint,
        chunker_version="chunk-v1",
        chunking_config="{}",
        lexical_normalizer_version="lex-v1",
        embedding_model="embedding-model",
        embedding_revision="embedding-revision",
        embedding_dimensions=3,
        retrieval_config="{}",
    )


def _publish(
    repository: RagRepository,
    generation: GenerationBuild,
    document_version_id: int,
    status: DocumentIndexStatus,
) -> None:
    repository.insert_document_index(
        generation.identity,
        expected_revision=generation.identity.content_revision,
        document_version_id=document_version_id,
        status=status,
        coverage_status=(
            CoverageStatus.NONE if status is not DocumentIndexStatus.READY else CoverageStatus.FULL
        ),
        searchable_page_count=0 if status is not DocumentIndexStatus.READY else 1,
        chunk_count=0 if status is not DocumentIndexStatus.READY else 1,
    )


def _active_generation(repository: RagRepository) -> GenerationBuild:
    generation = _create_generation(repository)
    repository.activate_generation(generation.identity, expected_revision=0)
    return generation


def test_query_normalization_is_bounded_and_conservative() -> None:
    assert normalize_query("  ＡＢＣ\tالعربية  ") == "abc العربية"


def test_query_normalization_rejects_non_text_and_empty_query() -> None:
    with pytest.raises(RagOperationalError) as caught:
        normalize_query("   ")
    assert caught.value.code is RagErrorCode.INVALID_QUERY_SCOPE
    with pytest.raises(RagOperationalError):
        normalize_query(12)  # type: ignore[arg-type]
    with pytest.raises(RagOperationalError):
        normalize_query("x" * (MAX_RAG_TEXT_CHARACTERS + 1))


def test_resolver_pins_query_and_immutable_authorized_ids(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        query = RagQuery("  ＡＢＣ  ", CourseScope(seeded["courses"][0]))  # type: ignore[index]

        snapshot = QuerySnapshotResolver(repository).resolve(query)

        assert snapshot.generation == generation.identity
        assert snapshot.normalized_query == "abc"
        assert snapshot.document_version_ids == (versions["course0_v1"],)
        with pytest.raises((AttributeError, TypeError)):
            snapshot.document_version_ids += (99,)  # type: ignore[misc]


def test_course_scope_never_leaks_sibling_course_and_global_is_explicit(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        for key in ("course0_v1", "course1_v1"):
            _publish(repository, generation, versions[key], DocumentIndexStatus.READY)

        course_snapshot = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        global_snapshot = repository.pin_query_snapshot(GlobalScope())

        assert course_snapshot.document_version_ids == (versions["course0_v1"],)
        assert global_snapshot.document_version_ids == (
            versions["course0_v1"],
            versions["course1_v1"],
        )


def test_preparing_and_failed_do_not_hide_ready_version(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        _publish(repository, generation, versions["course0_v2"], DocumentIndexStatus.PREPARING)

        snapshot = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        assert snapshot.document_version_ids == (versions["course0_v1"],)

        # A failed replacement is also ignored for publication visibility.
        with repository_database(tmp_path / "failed") as (other, other_sessions, _):
            other_seeded = _seed_documents(other_sessions)
            other_generation = _active_generation(other)
            other_versions = other_seeded["versions"]
            assert isinstance(other_versions, dict)
            _publish(
                other, other_generation, other_versions["course0_v1"], DocumentIndexStatus.READY
            )
            _publish(
                other, other_generation, other_versions["course0_v2"], DocumentIndexStatus.FAILED
            )
            other_snapshot = other.pin_query_snapshot(
                CourseScope(other_seeded["courses"][0])  # type: ignore[index]
            )
            assert other_snapshot.document_version_ids == (other_versions["course0_v1"],)


def test_ready_replacement_supersedes_old_ready(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        _publish(repository, generation, versions["course0_v2"], DocumentIndexStatus.READY)
        snapshot = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        assert snapshot.document_version_ids == (versions["course0_v2"],)


def test_not_searchable_supersedes_ready_without_fallback(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        _publish(repository, generation, versions["course0_v2"], DocumentIndexStatus.NOT_SEARCHABLE)
        snapshot = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        assert snapshot.document_version_ids == ()


def test_building_and_retired_generations_are_invisible_to_normal_scope(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        active = _active_generation(repository)
        building = _create_generation(repository, fingerprint="b" * 64)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, active, versions["course0_v1"], DocumentIndexStatus.READY)
        _publish(repository, building, versions["course0_v2"], DocumentIndexStatus.READY)
        snapshot = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        assert snapshot.generation.generation_id == active.generation_id
        assert snapshot.document_version_ids == (versions["course0_v1"],)


def test_historical_scope_prefers_active_then_newest_retired_generation(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        active = _active_generation(repository)
        _publish(repository, active, versions["course0_v1"], DocumentIndexStatus.READY)
        active_history = repository.pin_query_snapshot(HistoricalScope((versions["course0_v1"],)))
        assert active_history.generation.generation_id == active.generation_id

        repository.retire_generation(active.identity, expected_revision=0)
        replacement = _active_generation(repository)
        _publish(repository, replacement, versions["course0_v2"], DocumentIndexStatus.READY)
        retired_history = repository.pin_query_snapshot(HistoricalScope((versions["course0_v1"],)))
        assert retired_history.generation.generation_id == active.generation_id

        with pytest.raises(RagOperationalError) as caught:
            repository.pin_query_snapshot(
                HistoricalScope((versions["course0_v1"], versions["course1_v1"]))
            )
        assert caught.value.code is RagErrorCode.INVALID_QUERY_SCOPE


def test_historical_duplicate_or_unknown_version_fails_closed(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        with pytest.raises(RagOperationalError) as duplicate:
            repository.pin_query_snapshot(HistoricalScope((versions["course0_v1"],) * 2))
        assert duplicate.value.code is RagErrorCode.INVALID_QUERY_SCOPE
        with pytest.raises(RagOperationalError) as unknown:
            repository.pin_query_snapshot(HistoricalScope((999999,)))
        assert unknown.value.code is RagErrorCode.INVALID_QUERY_SCOPE


def test_snapshot_revision_is_pinned_across_later_publication(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        first = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]
        repository.bump_content_revision(generation.identity, expected_revision=0)
        second = repository.pin_query_snapshot(CourseScope(seeded["courses"][0]))  # type: ignore[index]

        assert (first.generation.content_revision, first.document_version_ids) == (
            0,
            (versions["course0_v1"],),
        )
        assert (second.generation.content_revision, second.document_version_ids) == (
            1,
            (versions["course0_v1"],),
        )


def test_no_active_generation_and_invalid_resolver_input_are_safe(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, _, _):
        with pytest.raises(RagOperationalError) as caught:
            repository.pin_query_snapshot(GlobalScope())
        assert caught.value.code is RagErrorCode.NO_ACTIVE_GENERATION
        with pytest.raises(RagOperationalError) as invalid:
            resolve_query_snapshot(repository, object())  # type: ignore[arg-type]
        assert invalid.value.code is RagErrorCode.INVALID_QUERY_SCOPE


class _EmbeddingStub:
    def __init__(self) -> None:
        self.released = False

    def model_metadata(self) -> EmbeddingModelMetadata:
        return EmbeddingModelMetadata("model", "revision", 3, "cpu")

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0, 0.0)

    def release_model(self) -> None:
        self.released = True

    def close(self) -> None:
        self.released = True


class _DenseStub:
    def __init__(self) -> None:
        self.allowed: tuple[int, ...] | None = None

    def upsert(self, records: tuple[object, ...]) -> None:
        return None

    def search(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
        allowed_document_version_ids: tuple[int, ...],
    ) -> tuple[DenseSearchHit, ...]:
        self.allowed = allowed_document_version_ids
        return (DenseSearchHit("a" * 64, 1, 0.0),)

    def contains(self, chunk_keys: tuple[str, ...]) -> bool:
        return True

    def count(self) -> int:
        return 1

    def close(self) -> None:
        return None


def test_hybrid_candidate_search_receives_frozen_snapshot_allowlist(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        seeded = _seed_documents(sessions)
        generation = _active_generation(repository)
        versions = seeded["versions"]
        assert isinstance(versions, dict)
        _publish(repository, generation, versions["course0_v1"], DocumentIndexStatus.READY)
        embedding = _EmbeddingStub()
        dense = _DenseStub()
        lexical_calls: list[tuple[str, tuple[int, ...], int]] = []

        def lexical_search(query: str, snapshot: object, limit: int) -> object:
            assert hasattr(snapshot, "document_version_ids")
            ids = snapshot.document_version_ids
            lexical_calls.append((query, ids, limit))
            from aaos.rag.lexical import LexicalSearchResult

            return LexicalSearchResult(query, ())

        retriever = HybridRetriever(
            repository,
            embedding,
            dense,
            lexical_search=lexical_search,  # type: ignore[arg-type]
        )
        candidates = retriever.retrieve_candidates(
            RagQuery("course", CourseScope(seeded["courses"][0]))  # type: ignore[index]
        )

        assert isinstance(candidates, RetrievalCandidateSet)
        assert dense.allowed == (versions["course0_v1"],)
        assert lexical_calls == [("course", (versions["course0_v1"],), 30)]
        assert embedding.released is True
