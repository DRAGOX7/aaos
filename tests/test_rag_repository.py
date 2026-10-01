"""Authoritative SQLite repository tests for the Phase 4 RAG boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
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
from aaos.persistence.rag_models import (
    RagChunkORM,
    RagChunkSourceSpanORM,
    RagDocumentIndexORM,
    RagIndexGenerationORM,
)
from aaos.rag.contracts import (
    CourseScope,
    CoverageStatus,
    DocumentIndexStatus,
    GenerationStatus,
    GlobalScope,
    HistoricalScope,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)
from aaos.rag.repository import (
    AuthoritativeRetrievalChunk,
    ChunkRecord,
    DocumentIndexRecord,
    DocumentPageRecord,
    DocumentVersionRecord,
    GenerationBuild,
    RagRepository,
    SourceSpanRecord,
    StructureSourceSpanRecord,
)


def _add_authoritative_retrieval_chunk(
    repository: RagRepository,
    generation: GenerationBuild,
    document_version_id: int,
    page: DocumentPageORM,
    *,
    chunk_key: str,
) -> ChunkRecord:
    repository.insert_document_index(
        generation.identity,
        expected_revision=generation.identity.content_revision,
        document_version_id=document_version_id,
        status=DocumentIndexStatus.READY,
        coverage_status=CoverageStatus.FULL,
        searchable_page_count=1,
        chunk_count=1,
    )
    chunk = repository.insert_chunk(
        generation.identity,
        expected_revision=generation.identity.content_revision,
        document_version_id=document_version_id,
        chunk_key=chunk_key,
        ordinal=1,
        heading=None,
        exact_text=page.text,
        token_count=1,
        text_sha256="a" * 64,
        page_start=1,
        page_end=1,
    )
    repository.insert_source_span(
        generation.identity,
        expected_revision=generation.identity.content_revision,
        rag_chunk_id=chunk.chunk_id,
        document_page_id=page.id,
        ordinal=1,
        page_number=1,
        page_char_start=0,
        page_char_end=len(page.text),
        chunk_char_start=0,
        chunk_char_end=len(page.text),
    )
    repository.rebuild_fts(
        generation.identity, expected_revision=generation.identity.content_revision
    )
    return chunk


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def repository_database(tmp_path: Path) -> Iterator[tuple[RagRepository, SessionFactory, Engine]]:
    database_path = tmp_path / "rag-repository.db"
    command.upgrade(_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield RagRepository(sessions), sessions, engine
    finally:
        engine.dispose()


def _seed_phase3(sessions: SessionFactory) -> dict[str, int]:
    with sessions() as session, session.begin():
        course_one = CourseORM(
            source_system="phase3",
            source_id="course-one",
            title="Course one",
            is_active=True,
        )
        course_two = CourseORM(
            source_system="phase3",
            source_id="course-two",
            title="Course two",
            is_active=True,
        )
        inactive_course = CourseORM(
            source_system="phase3",
            source_id="course-inactive",
            title="Inactive course",
            is_active=False,
        )
        session.add_all((course_one, course_two, inactive_course))
        session.flush()

        remote_one = RemoteFileORM(
            course_id=course_one.id,
            source_system="phase3",
            source_id="remote-one",
            title="One",
            is_active=True,
        )
        remote_two = RemoteFileORM(
            course_id=course_one.id,
            source_system="phase3",
            source_id="remote-two",
            title="Two",
            is_active=True,
        )
        remote_other_course = RemoteFileORM(
            course_id=course_two.id,
            source_system="phase3",
            source_id="remote-other-course",
            title="Other",
            is_active=True,
        )
        remote_inactive = RemoteFileORM(
            course_id=course_one.id,
            source_system="phase3",
            source_id="remote-inactive",
            title="Inactive remote",
            is_active=False,
        )
        remote_inactive_course = RemoteFileORM(
            course_id=inactive_course.id,
            source_system="phase3",
            source_id="remote-inactive-course",
            title="Inactive course remote",
            is_active=True,
        )
        session.add_all(
            (remote_one, remote_two, remote_other_course, remote_inactive, remote_inactive_course)
        )
        session.flush()

        documents: dict[int, DocumentORM] = {}

        def add_document(
            remote_file_id: int, version_number: int, *, ready: bool = True
        ) -> DocumentVersionORM:
            document = documents.get(remote_file_id)
            if document is None:
                document = DocumentORM(
                    remote_file_id=remote_file_id,
                    ingestion_status=IngestionStatus.READY.value if ready else "FAILED",
                    created_at=NOW,
                    updated_at=NOW,
                )
                documents[remote_file_id] = document
                session.add(document)
                session.flush()
            digest = (f"{remote_file_id:08x}{version_number:08x}").ljust(64, "a")
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
                source_filename=f"document-{remote_file_id}-{version_number}.pdf",
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
                    text=f"text {remote_file_id} {version_number}",
                    text_source="EMBEDDED_PDF",
                    width_points=612.0,
                    height_points=792.0,
                    created_at=NOW,
                )
            )
            return version

        one_v1 = add_document(remote_one.id, 1)
        one_v2 = add_document(remote_one.id, 2)
        two_v1 = add_document(remote_two.id, 1)
        other_course_v1 = add_document(remote_other_course.id, 1)
        add_document(remote_inactive.id, 1)
        add_document(remote_inactive_course.id, 1)
        session.flush()
        return {
            "course_one": course_one.id,
            "course_two": course_two.id,
            "one_v1": one_v1.id,
            "one_v2": one_v2.id,
            "two_v1": two_v1.id,
            "other_course_v1": other_course_v1.id,
        }


def _identity(
    generation_id: int, *, revision: int = 0, fingerprint: str = "a" * 64
) -> RagGenerationIdentity:
    return RagGenerationIdentity(
        generation_id=generation_id,
        config_fingerprint=fingerprint,
        content_revision=revision,
        embedding_dimensions=3,
    )


def _create_generation(repository: RagRepository) -> GenerationBuild:
    return repository.create_generation(
        config_fingerprint="a" * 64,
        chunker_version="chunk-v1",
        chunking_config="{}",
        lexical_normalizer_version="lex-v1",
        embedding_model="embedding-model",
        embedding_revision="embedding-revision",
        embedding_dimensions=3,
        retrieval_config="{}",
    )


def test_current_versions_select_latest_per_document_and_isolate_course(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)

        current = repository.resolve_current_versions(CourseScope(ids["course_one"]))
        assert tuple(item.document_version_id for item in current) == (ids["one_v2"], ids["two_v1"])
        assert all(isinstance(item, DocumentVersionRecord) for item in current)

        global_current = repository.resolve_current_versions(GlobalScope())
        assert tuple(item.document_version_id for item in global_current) == (
            ids["one_v2"],
            ids["two_v1"],
            ids["other_course_v1"],
        )


def test_historical_scope_explicitly_includes_superseded_version_only(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        historical = repository.resolve_historical_versions(
            HistoricalScope((ids["one_v1"], ids["one_v2"], ids["one_v1"]))
        )
        assert tuple(item.document_version_id for item in historical) == (
            ids["one_v1"],
            ids["one_v2"],
        )


def test_no_eligible_documents_is_safe_error(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        with sessions() as session, session.begin():
            course = CourseORM(source_system="phase3", source_id="empty", title="Empty")
            session.add(course)
        with pytest.raises(RagOperationalError) as caught:
            repository.resolve_current_versions(CourseScope(course.id))
        assert caught.value.code is RagErrorCode.NO_ELIGIBLE_DOCUMENTS
        assert str(caught.value) == "rag operation failed"


def test_generation_creation_activation_and_single_active_lookup(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, _, _):
        first = _create_generation(repository)
        first_identity = first.identity
        assert first.status is GenerationStatus.BUILDING
        repository.activate_generation(first_identity, expected_revision=0)
        assert repository.get_active_generation().generation_id == first.generation_id

        second = repository.create_generation(
            config_fingerprint="b" * 64,
            chunker_version="chunk-v1",
            chunking_config="{}",
            lexical_normalizer_version="lex-v1",
            embedding_model="embedding-model",
            embedding_revision="embedding-revision",
            embedding_dimensions=3,
            retrieval_config="{}",
        )
        repository.activate_generation(second.identity, expected_revision=0)
        assert repository.get_active_generation().generation_id == second.generation_id
        assert repository.get_generation(first.generation_id).status is GenerationStatus.RETIRED


def test_content_revision_compare_and_swap_rejects_stale_identity(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, _, _):
        generation = _create_generation(repository)
        updated = repository.bump_content_revision(generation.identity, expected_revision=0)
        assert updated.content_revision == 1
        with pytest.raises(RagOperationalError) as caught:
            repository.bump_content_revision(generation.identity, expected_revision=0)
        assert caught.value.code is RagErrorCode.GENERATION_ACTIVATION_FAILED


def test_document_indexes_chunks_and_spans_are_typed_ordered_and_unique(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        index = repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=2,
        )
        assert isinstance(index, DocumentIndexRecord)
        first = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            chunk_key="a" * 64,
            ordinal=1,
            heading=None,
            exact_text="first",
            token_count=1,
            text_sha256="b" * 64,
            page_start=1,
            page_end=1,
        )
        second = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            chunk_key="c" * 64,
            ordinal=2,
            heading="Heading",
            exact_text="text 1 2",
            token_count=3,
            text_sha256="d" * 64,
            page_start=1,
            page_end=1,
        )
        assert tuple(item.ordinal for item in repository.list_chunks(generation.identity)) == (1, 2)
        assert isinstance(first, ChunkRecord) and isinstance(second, ChunkRecord)
        (page,) = repository.read_pages(ids["one_v2"])
        span = repository.insert_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=second.chunk_id,
            document_page_id=page.document_page_id,
            ordinal=1,
            page_number=1,
            page_char_start=0,
            page_char_end=8,
            chunk_char_start=0,
            chunk_char_end=8,
        )
        assert isinstance(span, SourceSpanRecord)
        assert repository.list_source_spans(generation.identity, second.chunk_id) == (span,)
        with pytest.raises(RagOperationalError) as caught:
            repository.insert_chunk(
                generation.identity,
                expected_revision=0,
                document_version_id=ids["one_v2"],
                chunk_key="a" * 64,
                ordinal=3,
                heading=None,
                exact_text="duplicate",
                token_count=1,
                text_sha256="e" * 64,
                page_start=1,
                page_end=1,
            )
        assert caught.value.code is RagErrorCode.DOCUMENT_INDEX_FAILED
        assert repository.get_document_index(generation.identity, ids["one_v2"]) == index


def test_source_span_requires_exact_chunk_page_provenance(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        chunk = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            chunk_key="f" * 64,
            ordinal=1,
            heading=None,
            exact_text="other",
            token_count=1,
            text_sha256="1" * 64,
            page_start=1,
            page_end=1,
        )
        (page,) = repository.read_pages(ids["one_v2"])
        with pytest.raises(RagOperationalError) as caught:
            repository.insert_source_span(
                generation.identity,
                expected_revision=0,
                rag_chunk_id=chunk.chunk_id,
                document_page_id=999,
                ordinal=1,
                page_number=1,
                page_char_start=0,
                page_char_end=4,
                chunk_char_start=0,
                chunk_char_end=4,
            )
        assert caught.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED
        with pytest.raises(RagOperationalError) as caught:
            repository.insert_source_span(
                generation.identity,
                expected_revision=0,
                rag_chunk_id=chunk.chunk_id,
                document_page_id=page.document_page_id,
                ordinal=1,
                page_number=1,
                page_char_start=0,
                page_char_end=4,
                chunk_char_start=0,
                chunk_char_end=4,
            )
        assert caught.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED


def test_chunk_page_bounds_must_fit_the_phase3_version(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        with pytest.raises(RagOperationalError) as caught:
            repository.insert_chunk(
                generation.identity,
                expected_revision=0,
                document_version_id=ids["one_v2"],
                chunk_key="2" * 64,
                ordinal=1,
                heading=None,
                exact_text="text",
                token_count=1,
                text_sha256="3" * 64,
                page_start=2,
                page_end=2,
            )
        assert caught.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED


def test_source_span_offsets_must_be_in_bounds_and_reconstruct_text(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        chunk = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            chunk_key="4" * 64,
            ordinal=1,
            heading=None,
            exact_text="different",
            token_count=1,
            text_sha256="5" * 64,
            page_start=1,
            page_end=1,
        )
        (page,) = repository.read_pages(ids["one_v2"])
        with pytest.raises(RagOperationalError) as caught:
            repository.insert_source_span(
                generation.identity,
                expected_revision=0,
                rag_chunk_id=chunk.chunk_id,
                document_page_id=page.document_page_id,
                ordinal=1,
                page_number=1,
                page_char_start=0,
                page_char_end=len(page.text) + 1,
                chunk_char_start=0,
                chunk_char_end=len(chunk.exact_text),
            )
        assert caught.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED


def test_query_snapshot_pins_active_generation_and_scope(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=1,
        )
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["two_v1"],
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=1,
        )
        snapshot = repository.pin_query_snapshot(CourseScope(ids["course_one"]))
        assert snapshot.generation.generation_id == generation.generation_id
        assert snapshot.generation.content_revision == 0
        assert snapshot.document_version_ids == (ids["one_v2"], ids["two_v1"])


def test_query_snapshot_rejects_generation_without_ready_indexes(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        with pytest.raises(RagOperationalError) as caught:
            repository.pin_query_snapshot(CourseScope(ids["course_one"]))
        assert caught.value.code is RagErrorCode.NO_ELIGIBLE_DOCUMENTS


def test_materialize_snapshot_chunks_returns_requested_order_and_authority(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        with sessions() as session:
            pages = {
                int(page.document_version_id): page for page in session.query(DocumentPageORM).all()
            }
        first = _add_authoritative_retrieval_chunk(
            repository,
            generation,
            ids["one_v2"],
            pages[ids["one_v2"]],
            chunk_key="1" * 64,
        )
        second = _add_authoritative_retrieval_chunk(
            repository,
            generation,
            ids["two_v1"],
            pages[ids["two_v1"]],
            chunk_key="2" * 64,
        )
        snapshot = repository.pin_query_snapshot(GlobalScope())

        single = repository.materialize_snapshot_chunks(snapshot, (first.chunk_key,))
        assert tuple(item.chunk.chunk_key for item in single) == (first.chunk_key,)
        materialized = repository.materialize_snapshot_chunks(
            snapshot, (second.chunk_key, first.chunk_key)
        )

        assert tuple(item.chunk.chunk_key for item in materialized) == (
            second.chunk_key,
            first.chunk_key,
        )
        assert all(isinstance(item, AuthoritativeRetrievalChunk) for item in materialized)
        assert tuple(item.document_version.document_version_id for item in materialized) == (
            ids["two_v1"],
            ids["one_v2"],
        )
        assert all(item.source_spans for item in materialized)


def test_materialize_snapshot_chunks_rejects_empty_duplicates_and_missing_keys(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        with sessions() as session:
            page = session.query(DocumentPageORM).filter_by(document_version_id=ids["one_v2"]).one()
        chunk = _add_authoritative_retrieval_chunk(
            repository,
            generation,
            ids["one_v2"],
            page,
            chunk_key="3" * 64,
        )
        snapshot = repository.pin_query_snapshot(CourseScope(ids["course_one"]))

        assert repository.materialize_snapshot_chunks(snapshot, ()) == ()
        for keys in ((chunk.chunk_key, chunk.chunk_key), ("f" * 64,)):
            with pytest.raises(RagOperationalError) as caught:
                repository.materialize_snapshot_chunks(snapshot, keys)
            assert caught.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_materialize_snapshot_chunks_survives_revision_and_retirement_but_rejects_building(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.activate_generation(generation.identity, expected_revision=0)
        with sessions() as session:
            page = session.query(DocumentPageORM).filter_by(document_version_id=ids["one_v2"]).one()
        chunk = _add_authoritative_retrieval_chunk(
            repository,
            generation,
            ids["one_v2"],
            page,
            chunk_key="4" * 64,
        )
        snapshot = repository.pin_query_snapshot(CourseScope(ids["course_one"]))
        revised = repository.bump_content_revision(generation.identity, expected_revision=0)

        assert (
            repository.materialize_snapshot_chunks(snapshot, (chunk.chunk_key,))[0].chunk == chunk
        )

        repository.retire_generation(revised, expected_revision=1)
        assert (
            repository.materialize_snapshot_chunks(snapshot, (chunk.chunk_key,))[0].chunk == chunk
        )

        with sessions() as session, session.begin():
            session.execute(
                update(RagIndexGenerationORM)
                .where(RagIndexGenerationORM.id == generation.generation_id)
                .values(status=GenerationStatus.BUILDING.value)
            )
        with pytest.raises(RagOperationalError) as caught:
            repository.materialize_snapshot_chunks(snapshot, (chunk.chunk_key,))
        assert caught.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_phase3_rows_remain_unchanged_by_rag_repository(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        ids = _seed_phase3(sessions)
        with engine.connect() as connection:
            before = tuple(
                connection.execute(
                    select(DocumentORM.id, DocumentORM.updated_at).order_by(DocumentORM.id)
                )
            )
        generation = _create_generation(repository)
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            status=DocumentIndexStatus.PREPARING,
            coverage_status=CoverageStatus.NONE,
            searchable_page_count=0,
            chunk_count=0,
        )
        with engine.connect() as connection:
            after = tuple(
                connection.execute(
                    select(DocumentORM.id, DocumentORM.updated_at).order_by(DocumentORM.id)
                )
            )
        assert before == after
        assert ids["one_v2"] > 0


def test_page_records_are_immutable_typed_values(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        pages = repository.read_pages(ids["one_v2"])
        assert pages and isinstance(pages[0], DocumentPageRecord)
        with pytest.raises((AttributeError, TypeError)):
            setattr(pages[0], "text", "mutated")


def test_no_active_generation_is_safe_error(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, _, _):
        with pytest.raises(RagOperationalError) as caught:
            repository.get_active_generation()
        assert caught.value.code is RagErrorCode.NO_ACTIVE_GENERATION


def test_document_index_duplicate_rolls_back_without_partial_rows(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions, engine):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=1,
        )
        with pytest.raises(RagOperationalError):
            repository.insert_document_index(
                generation.identity,
                expected_revision=0,
                document_version_id=ids["one_v2"],
                status=DocumentIndexStatus.READY,
                coverage_status=CoverageStatus.FULL,
                searchable_page_count=1,
                chunk_count=1,
            )
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(RagDocumentIndexORM)) == 1
            assert connection.scalar(select(func.count()).select_from(RagChunkORM)) == 0
            assert connection.scalar(select(func.count()).select_from(RagChunkSourceSpanORM)) == 0


def test_generation_document_verification_rejects_non_ready_or_incomplete_rows(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        generation = _create_generation(repository)
        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=ids["one_v2"],
            status=DocumentIndexStatus.PREPARING,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=1,
            chunk_count=1,
        )
        with pytest.raises(RagOperationalError) as caught:
            repository.verify_generation_document(generation.identity, ids["one_v2"])
        assert caught.value.code is RagErrorCode.INDEX_INTEGRITY_FAILED


def test_explicit_none_active_expectation_rejects_competing_active_generation(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, _, _):
        current = _create_generation(repository)
        repository.activate_generation(current.identity, expected_revision=0)

        candidate = _create_generation(repository)

        with pytest.raises(RagOperationalError) as caught:
            repository.activate_generation(
                candidate.identity,
                expected_revision=0,
                expected_active_generation_id=None,
            )

        assert caught.value.code is RagErrorCode.GENERATION_ACTIVATION_FAILED
        assert repository.get_active_generation().generation_id == current.generation_id
        assert (
            repository.get_generation(candidate.generation_id).status is GenerationStatus.BUILDING
        )


def _convert_version_to_structure_for_rag_test(
    sessions: SessionFactory,
    document_version_id: int,
) -> tuple[int, int, int]:
    # Convert one existing PAGE fixture into a valid STRUCTURE fixture.
    with sessions() as session, session.begin():
        version = session.get(
            DocumentVersionORM,
            document_version_id,
        )
        assert version is not None

        session.query(DocumentPageORM).filter(
            DocumentPageORM.document_version_id == document_version_id
        ).delete(synchronize_session=False)

        digest = sha256(f"structural-{document_version_id}".encode("utf-8")).hexdigest()

        blob = ContentBlobORM(
            sha256=digest,
            size_bytes=128,
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            storage_key=f"sha256/{digest[:2]}/{digest}.docx",
            created_at=NOW,
        )
        session.add(blob)
        session.flush()

        version.content_blob_id = blob.id
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
        table_cell = DocumentStructureUnitORM(
            document_version_id=document_version_id,
            ordinal=3,
            kind="TABLE_CELL",
            text="Adam",
            heading_level=None,
            table_index=0,
            row_index=0,
            column_index=0,
            created_at=NOW,
        )

        session.add_all(
            (
                heading,
                paragraph,
                table_cell,
            )
        )
        session.flush()

        return (
            int(heading.id),
            int(paragraph.id),
            int(table_cell.id),
        )


def test_structural_version_is_an_index_new_candidate(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        version_id = ids["one_v2"]

        _convert_version_to_structure_for_rag_test(
            sessions,
            version_id,
        )

        generation = _create_generation(repository)
        active = repository.activate_generation(
            generation.identity,
            expected_revision=0,
        )

        candidates = repository.resolve_index_new_candidates(
            active.identity,
            version_id,
        )

        assert len(candidates) == 1

        candidate = candidates[0]
        assert candidate.document_version_id == version_id
        assert candidate.provenance_mode == "STRUCTURE"
        assert candidate.page_count is None
        assert candidate.source_filename.endswith(".docx")


def test_repository_reads_ordered_structural_units(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        version_id = ids["one_v2"]

        expected_ids = _convert_version_to_structure_for_rag_test(
            sessions,
            version_id,
        )

        units = repository.read_structure_units(
            version_id,
        )

        assert tuple(unit.document_structure_unit_id for unit in units) == expected_ids

        assert tuple(unit.ordinal for unit in units) == (1, 2, 3)

        assert tuple(unit.kind for unit in units) == (
            "HEADING",
            "PARAGRAPH",
            "TABLE_CELL",
        )

        assert tuple(unit.text for unit in units) == (
            "Neural Networks",
            "A neural network learns patterns.",
            "Adam",
        )

        assert units[0].heading_level == 1

        assert (
            units[2].table_index,
            units[2].row_index,
            units[2].column_index,
        ) == (0, 0, 0)


def test_repository_persists_and_verifies_structural_chunk(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        version_id = ids["one_v2"]

        heading_id, paragraph_id, _ = _convert_version_to_structure_for_rag_test(
            sessions,
            version_id,
        )

        generation = _create_generation(repository)

        repository.begin_document_preparation(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
        )

        exact_text = "Neural Networks\n\nA neural network learns patterns."

        chunk = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            chunk_key="8" * 64,
            ordinal=1,
            heading="Neural Networks",
            exact_text=exact_text,
            token_count=7,
            text_sha256="7" * 64,
            provenance_mode="STRUCTURE",
            page_start=None,
            page_end=None,
        )

        heading_text = "Neural Networks"

        repository.insert_structure_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=chunk.chunk_id,
            document_structure_unit_id=heading_id,
            ordinal=1,
            source_char_start=0,
            source_char_end=len(heading_text),
            chunk_char_start=0,
            chunk_char_end=len(heading_text),
        )

        paragraph_text = "A neural network learns patterns."
        paragraph_start = len("Neural Networks\n\n")

        repository.insert_structure_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=chunk.chunk_id,
            document_structure_unit_id=paragraph_id,
            ordinal=2,
            source_char_start=0,
            source_char_end=len(paragraph_text),
            chunk_char_start=paragraph_start,
            chunk_char_end=len(exact_text),
        )

        assert repository.list_chunks(
            generation.identity,
            version_id,
        ) == (chunk,)

        assert chunk.provenance_mode == "STRUCTURE"
        assert chunk.page_start is None
        assert chunk.page_end is None

        verification = repository.verify_generation_document(
            generation.identity,
            version_id,
        )

        assert verification.chunk_count == 1
        assert verification.source_span_count == 2
        assert verification.chunk_keys == (chunk.chunk_key,)


def test_structural_retry_cleanup_removes_derived_provenance(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        version_id = ids["one_v2"]

        heading_id, _, _ = _convert_version_to_structure_for_rag_test(
            sessions,
            version_id,
        )

        generation = _create_generation(repository)

        repository.begin_document_preparation(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
        )

        chunk = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            chunk_key="6" * 64,
            ordinal=1,
            heading="Neural Networks",
            exact_text="Neural Networks",
            token_count=2,
            text_sha256="5" * 64,
            provenance_mode="STRUCTURE",
            page_start=None,
            page_end=None,
        )

        repository.insert_structure_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=chunk.chunk_id,
            document_structure_unit_id=heading_id,
            ordinal=1,
            source_char_start=0,
            source_char_end=len("Neural Networks"),
            chunk_char_start=0,
            chunk_char_end=len("Neural Networks"),
        )

        # Retrying must remove structural spans before
        # deleting their parent chunk.
        repository.begin_document_preparation(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
        )

        assert (
            repository.list_chunks(
                generation.identity,
                version_id,
            )
            == ()
        )


def test_materialize_snapshot_chunks_returns_structural_provenance(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        version_id = ids["one_v2"]

        heading_id, paragraph_id, _ = _convert_version_to_structure_for_rag_test(
            sessions,
            version_id,
        )

        generation = _create_generation(repository)
        repository.activate_generation(
            generation.identity,
            expected_revision=0,
        )

        repository.insert_document_index(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            status=DocumentIndexStatus.READY,
            coverage_status=CoverageStatus.FULL,
            searchable_page_count=0,
            chunk_count=1,
        )

        exact_text = "Neural Networks\n\nA neural network learns patterns."

        chunk = repository.insert_chunk(
            generation.identity,
            expected_revision=0,
            document_version_id=version_id,
            chunk_key="9" * 64,
            ordinal=1,
            heading="Neural Networks",
            exact_text=exact_text,
            token_count=7,
            text_sha256="6" * 64,
            provenance_mode="STRUCTURE",
            page_start=None,
            page_end=None,
        )

        heading_text = "Neural Networks"
        repository.insert_structure_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=chunk.chunk_id,
            document_structure_unit_id=heading_id,
            ordinal=1,
            source_char_start=0,
            source_char_end=len(heading_text),
            chunk_char_start=0,
            chunk_char_end=len(heading_text),
        )

        paragraph_text = "A neural network learns patterns."
        paragraph_start = len("Neural Networks\n\n")

        repository.insert_structure_source_span(
            generation.identity,
            expected_revision=0,
            rag_chunk_id=chunk.chunk_id,
            document_structure_unit_id=paragraph_id,
            ordinal=2,
            source_char_start=0,
            source_char_end=len(paragraph_text),
            chunk_char_start=paragraph_start,
            chunk_char_end=len(exact_text),
        )

        snapshot = repository.pin_query_snapshot(CourseScope(ids["course_one"]))

        materialized = repository.materialize_snapshot_chunks(
            snapshot,
            (chunk.chunk_key,),
        )

        assert len(materialized) == 1

        item = materialized[0]

        assert item.chunk == chunk
        assert item.chunk.provenance_mode == "STRUCTURE"
        assert item.document_version.provenance_mode == "STRUCTURE"

        assert len(item.source_spans) == 2

        assert all(
            isinstance(
                span,
                StructureSourceSpanRecord,
            )
            for span in item.source_spans
        )

        first, second = item.source_spans

        assert isinstance(first, StructureSourceSpanRecord)
        assert isinstance(second, StructureSourceSpanRecord)
        assert first.document_structure_unit_id == heading_id
        assert first.unit_ordinal == 1
        assert first.kind == "HEADING"
        assert first.heading_level == 1

        assert second.document_structure_unit_id == paragraph_id
        assert second.unit_ordinal == 2
        assert second.kind == "PARAGRAPH"
        assert second.heading_level is None

        for span in item.source_spans:
            assert not hasattr(span, "page_number")
            assert not hasattr(span, "document_page_id")


def _share_content(sessions: SessionFactory, source_version_id: int, *version_ids: int) -> None:
    with sessions() as session, session.begin():
        blob_id = session.scalar(
            select(DocumentVersionORM.content_blob_id).where(
                DocumentVersionORM.id == source_version_id
            )
        )
        session.execute(
            update(DocumentVersionORM)
            .where(DocumentVersionORM.id.in_(version_ids))
            .values(content_blob_id=blob_id)
        )


def test_identical_files_in_one_course_are_indexed_once(tmp_path: Path) -> None:
    """Regression: the same syllabus posted twice was indexed twice, splitting citations."""
    with repository_database(tmp_path) as (repository, sessions, _):
        ids = _seed_phase3(sessions)
        _share_content(sessions, ids["one_v2"], ids["two_v1"], ids["other_course_v1"])

        rebuild = repository.resolve_rebuild_candidates()
        generation = _create_generation(repository)
        active = repository.activate_generation(generation.identity, expected_revision=0)
        index_new = repository.resolve_index_new_candidates(active.identity)

        for candidates in (rebuild, index_new):
            chosen = {item.document_version_id for item in candidates}
            assert ids["one_v2"] in chosen
            assert ids["two_v1"] not in chosen
            # The same file in another course stays searchable in that course.
            assert ids["other_course_v1"] in chosen
