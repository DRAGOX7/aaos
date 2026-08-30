"""Lease-protected, per-document cross-store RAG indexing orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aaos.rag.chunking import DeterministicChunker
from aaos.rag.contracts import (
    CoverageStatus,
    DenseIndexFactory,
    DenseVectorRecord,
    DocumentIndexStatus,
    EmbeddingService,
    IndexRunResult,
    RagErrorCode,
    RagGenerationIdentity,
    RagIndexStatus,
    RagOperationalError,
)
from aaos.rag.lock import RagIndexLease, RagIndexLock
from aaos.rag.repository import (
    ChunkRecord,
    DocumentVerificationRecord,
    GenerationBuild,
    GenerationIntegrityReport,
    RagRepository,
    StagedDocumentIndexResult,
)


@dataclass(frozen=True, slots=True)
class DocumentIndexResult:
    """Safe result of preparing one document for later atomic publication."""

    document_version_id: int
    status: DocumentIndexStatus
    coverage_status: CoverageStatus
    searchable_page_count: int
    chunk_count: int
    error_code: RagErrorCode | None
    verification: DocumentVerificationRecord | None


class RagDocumentIndexer:
    """Prepare one BUILDING-generation document while holding the writer lease."""

    def __init__(
        self,
        repository: RagRepository,
        chunker: DeterministicChunker,
        embedding_service: EmbeddingService,
        dense_factory: DenseIndexFactory,
        index_lock: RagIndexLock,
    ) -> None:
        self._repository = repository
        self._chunker = chunker
        self._embedding_service = embedding_service
        self._dense_factory = dense_factory
        self._index_lock = index_lock

    def prepare_document(
        self,
        generation: RagGenerationIdentity,
        document_version_id: int,
        *,
        lease: RagIndexLease | None = None,
    ) -> DocumentIndexResult:
        """Persist, vectorize, and verify one document without making it query-visible."""
        if lease is None:
            with self._index_lock.acquire():
                result = self._prepare(generation, document_version_id, lease=None, building=True)
        else:
            self._index_lock.assert_owned(lease)
            result = self._prepare(generation, document_version_id, lease=lease, building=True)
        if isinstance(result, DocumentIndexResult):
            return result
        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)

    def stage_document_for_publication(
        self,
        lease: RagIndexLease,
        generation: RagGenerationIdentity,
        document_version_id: int,
    ) -> StagedDocumentIndexResult:
        """Stage one ACTIVE document under the caller's live outer lease."""
        self._index_lock.assert_owned(lease)
        result = self._prepare(generation, document_version_id, lease=lease, building=False)
        assert isinstance(result, StagedDocumentIndexResult)
        return result

    def release_embedding_model(self) -> None:
        """Release only the batch-scoped embedding model allocation."""
        self._embedding_service.release_model()

    def verify_dense_generation(
        self, generation: RagGenerationIdentity, expected_chunk_keys: tuple[str, ...]
    ) -> tuple[int, bool]:
        """Verify dense IDs for one generation without exposing the index handle."""
        dense = self._dense_factory.open(generation)
        try:
            count = dense.count()
            return count, count == len(expected_chunk_keys) and dense.contains(expected_chunk_keys)
        finally:
            dense.close()

    def _assert_lease(self, lease: RagIndexLease | None) -> None:
        if lease is not None:
            self._index_lock.assert_owned(lease)

    def _prepare(
        self,
        generation: RagGenerationIdentity,
        document_version_id: int,
        *,
        lease: RagIndexLease | None,
        building: bool,
    ) -> DocumentIndexResult | StagedDocumentIndexResult:
        preparing_started = False
        try:
            self._assert_lease(lease)
            build = self._repository.get_generation(generation.generation_id)
            metadata = self._embedding_service.model_metadata()
            if (
                build.identity != generation
                or build.status.value != ("BUILDING" if building else "ACTIVE")
                or self._chunker.config_fingerprint != generation.config_fingerprint
                or metadata.model_identifier != build.embedding_model
                or metadata.model_revision != build.embedding_revision
                or metadata.dimensions != generation.embedding_dimensions
            ):
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            version = self._repository.get_eligible_document_version(document_version_id)
            pages = self._repository.read_pages(document_version_id)
            self._assert_lease(lease)
            begin = (
                self._repository.begin_document_preparation
                if building
                else self._repository.begin_document_staging
            )
            begin(
                generation,
                expected_revision=generation.content_revision,
                document_version_id=document_version_id,
            )
            preparing_started = True
            chunks = tuple(
                self._chunker.chunk(
                    document_version_id,
                    pages,
                    version.content_sha256,
                )
            )
            searchable_pages = sum(1 for page in pages if page.text.strip())
            coverage = (
                CoverageStatus.NONE
                if searchable_pages == 0
                else CoverageStatus.FULL
                if searchable_pages == len(pages)
                else CoverageStatus.PARTIAL
            )
            if coverage is CoverageStatus.NONE:
                self._assert_lease(lease)
                if building:
                    self._repository.finish_document_preparation(
                        generation,
                        expected_revision=generation.content_revision,
                        document_version_id=document_version_id,
                        status=DocumentIndexStatus.NOT_SEARCHABLE,
                        coverage_status=coverage,
                        searchable_page_count=0,
                        chunk_count=0,
                    )
                else:
                    self._repository.record_staged_document(
                        generation,
                        expected_revision=generation.content_revision,
                        document_version_id=document_version_id,
                        intended_status=DocumentIndexStatus.NOT_SEARCHABLE,
                        coverage_status=coverage,
                        searchable_page_count=0,
                        chunk_count=0,
                        verification=None,
                    )
                    return StagedDocumentIndexResult(
                        generation,
                        document_version_id,
                        DocumentIndexStatus.NOT_SEARCHABLE,
                        coverage,
                        0,
                        0,
                        None,
                    )
                return DocumentIndexResult(
                    document_version_id,
                    DocumentIndexStatus.NOT_SEARCHABLE,
                    coverage,
                    0,
                    0,
                    None,
                    None,
                )
            stored_chunks: list[ChunkRecord] = []
            for draft in chunks:
                self._assert_lease(lease)
                chunk = self._repository.insert_chunk(
                    generation,
                    expected_revision=generation.content_revision,
                    document_version_id=document_version_id,
                    chunk_key=draft.chunk_key,
                    ordinal=draft.ordinal,
                    heading=draft.heading,
                    exact_text=draft.exact_text,
                    token_count=draft.token_count,
                    text_sha256=draft.text_sha256,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                )
                stored_chunks.append(chunk)
                for span_ordinal, span in enumerate(draft.source_spans, start=1):
                    self._assert_lease(lease)
                    self._repository.insert_source_span(
                        generation,
                        expected_revision=generation.content_revision,
                        rag_chunk_id=chunk.chunk_id,
                        document_page_id=span.document_page_id,
                        ordinal=span_ordinal,
                        page_number=span.page_number,
                        page_char_start=span.source_char_start,
                        page_char_end=span.source_char_end,
                        chunk_char_start=span.chunk_char_start,
                        chunk_char_end=span.chunk_char_end,
                    )
            try:
                self._assert_lease(lease)
                vectors = self._embedding_service.embed_documents(
                    tuple(chunk.exact_text for chunk in chunks)
                )
            except RagOperationalError:
                raise
            except Exception as error:
                raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED) from error
            self._validate_vectors(vectors, chunks, generation.embedding_dimensions)
            try:
                self._assert_lease(lease)
                dense = self._dense_factory.open(generation)
                try:
                    self._assert_lease(lease)
                    dense.upsert(
                        tuple(
                            DenseVectorRecord(chunk.chunk_key, document_version_id, vector)
                            for chunk, vector in zip(stored_chunks, vectors, strict=True)
                        )
                    )
                    self._assert_lease(lease)
                    verification = self._repository.verify_generation_document(
                        generation, document_version_id
                    )
                    if not dense.contains(verification.chunk_keys):
                        raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
                finally:
                    dense.close()
            except RagOperationalError:
                raise
            except Exception as error:
                raise RagOperationalError(RagErrorCode.DENSE_INDEX_WRITE_FAILED) from error
            self._assert_lease(lease)
            if building:
                self._repository.finish_document_preparation(
                    generation,
                    expected_revision=generation.content_revision,
                    document_version_id=document_version_id,
                    status=DocumentIndexStatus.READY,
                    coverage_status=coverage,
                    searchable_page_count=searchable_pages,
                    chunk_count=len(chunks),
                    verification=verification,
                )
            else:
                self._repository.record_staged_document(
                    generation,
                    expected_revision=generation.content_revision,
                    document_version_id=document_version_id,
                    intended_status=DocumentIndexStatus.READY,
                    coverage_status=coverage,
                    searchable_page_count=searchable_pages,
                    chunk_count=len(chunks),
                    verification=verification,
                )
                return StagedDocumentIndexResult(
                    generation,
                    document_version_id,
                    DocumentIndexStatus.READY,
                    coverage,
                    searchable_pages,
                    len(chunks),
                    verification,
                )
            return DocumentIndexResult(
                document_version_id,
                DocumentIndexStatus.READY,
                coverage,
                searchable_pages,
                len(chunks),
                None,
                verification,
            )
        except Exception as error:
            code = self._safe_failure_code(error)
            if not preparing_started:
                raise RagOperationalError(code) from error
            try:
                self._assert_lease(lease)
                self._repository.finish_document_preparation(
                    generation,
                    expected_revision=generation.content_revision,
                    document_version_id=document_version_id,
                    status=DocumentIndexStatus.FAILED,
                    coverage_status=CoverageStatus.NONE,
                    searchable_page_count=0,
                    chunk_count=0,
                    error_code=code,
                )
            except RagOperationalError as persistence_error:
                raise RagOperationalError(code) from persistence_error
            if lease is not None:
                raise RagOperationalError(code) from error
            return DocumentIndexResult(
                document_version_id,
                DocumentIndexStatus.FAILED,
                CoverageStatus.NONE,
                0,
                0,
                code,
                None,
            )

    @staticmethod
    def _validate_vectors(
        vectors: tuple[tuple[float, ...], ...], chunks: tuple[object, ...], dimensions: int
    ) -> None:
        if len(vectors) != len(chunks) or any(len(vector) != dimensions for vector in vectors):
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)

    @staticmethod
    def _safe_failure_code(error: Exception) -> RagErrorCode:
        if isinstance(error, RagOperationalError):
            if error.code in {
                RagErrorCode.EMBEDDING_FAILED,
                RagErrorCode.DENSE_INDEX_WRITE_FAILED,
                RagErrorCode.FTS_INDEX_FAILED,
                RagErrorCode.INDEX_INTEGRITY_FAILED,
                RagErrorCode.PROVENANCE_RESOLUTION_FAILED,
            }:
                return error.code
        return RagErrorCode.DOCUMENT_INDEX_FAILED


class RagIndexingCoordinator:
    """Atomic incremental publication and blue/green rebuild orchestration."""

    def __init__(
        self,
        repository: RagRepository,
        document_indexer: RagDocumentIndexer,
        index_lock: RagIndexLock,
    ) -> None:
        self._repository = repository
        self._document_indexer = document_indexer
        self._index_lock = index_lock

    def index_new(self, document_version_id: int | None = None) -> IndexRunResult:
        """Stage current candidates serially and publish once, or return no-op."""
        with self._index_lock.acquire() as lease:
            generation = self._repository.get_active_generation()
            candidates = self._repository.resolve_index_new_candidates(
                generation.identity, document_version_id
            )
            if not candidates:
                return IndexRunResult(
                    "NO_OP",
                    generation.generation_id,
                    generation.identity.content_revision,
                    0,
                    0,
                )
            staged: list[StagedDocumentIndexResult] = []
            try:
                for candidate in candidates:
                    staged.append(
                        self._document_indexer.stage_document_for_publication(
                            lease, generation.identity, candidate.document_version_id
                        )
                    )
            finally:
                self._document_indexer.release_embedding_model()
            self._index_lock.assert_owned(lease)
            revised = self._repository.publish_staged_documents(
                generation.identity,
                expected_revision=generation.identity.content_revision,
                staged_documents=tuple(staged),
            )
            return IndexRunResult(
                "PASS",
                revised.generation_id,
                revised.content_revision,
                sum(item.intended_status is DocumentIndexStatus.READY for item in staged),
                sum(item.intended_status is DocumentIndexStatus.NOT_SEARCHABLE for item in staged),
            )

    def rebuild(self) -> IndexRunResult:
        """Build an isolated generation and activate it only after integrity checks."""
        with self._index_lock.acquire() as lease:
            active = self._repository.get_active_generation()
            candidates = self._repository.resolve_rebuild_candidates()
            if not candidates:
                raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
            building = self._resolve_or_create_building(active)
            existing_indexes = {
                item.document_version_id: item
                for item in self._repository.list_document_indexes(building.identity)
            }
            try:
                for candidate in candidates:
                    existing = existing_indexes.get(candidate.document_version_id)
                    if existing is not None and existing.status in {
                        DocumentIndexStatus.READY,
                        DocumentIndexStatus.NOT_SEARCHABLE,
                    }:
                        continue
                    prepared = self._document_indexer.prepare_document(
                        building.identity,
                        candidate.document_version_id,
                        lease=lease,
                    )
                    if prepared.status not in {
                        DocumentIndexStatus.READY,
                        DocumentIndexStatus.NOT_SEARCHABLE,
                    }:
                        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            finally:
                self._document_indexer.release_embedding_model()
            self._index_lock.assert_owned(lease)
            report = self.verify_generation(
                building.identity,
                tuple(candidate.document_version_id for candidate in candidates),
            )
            if not report.valid or not report.dense_index_healthy:
                raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
            self._index_lock.assert_owned(lease)
            activated = self._repository.activate_generation(
                building.identity,
                expected_revision=building.identity.content_revision,
                expected_active_generation_id=active.generation_id,
            )
            return IndexRunResult(
                "PASS",
                activated.generation_id,
                activated.identity.content_revision,
                report.ready_document_count,
                report.not_searchable_document_count,
            )

    def _resolve_or_create_building(self, active: GenerationBuild) -> GenerationBuild:
        """Reuse one matching interrupted build or create an isolated BUILDING row."""
        buildings = self._repository.list_building_generations()
        if len(buildings) > 1:
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        if buildings:
            candidate = buildings[0]
            if (
                candidate.identity.config_fingerprint != active.identity.config_fingerprint
                or candidate.identity.embedding_dimensions != active.identity.embedding_dimensions
                or candidate.embedding_model != active.embedding_model
                or candidate.embedding_revision != active.embedding_revision
                or candidate.chunker_version != active.chunker_version
                or candidate.lexical_normalizer_version != active.lexical_normalizer_version
            ):
                raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
            return candidate
        return self._repository.create_generation(
            config_fingerprint=active.identity.config_fingerprint,
            chunker_version=active.chunker_version,
            chunking_config=active.chunking_config,
            lexical_normalizer_version=active.lexical_normalizer_version,
            embedding_model=active.embedding_model,
            embedding_revision=active.embedding_revision,
            embedding_dimensions=active.identity.embedding_dimensions,
            retrieval_config=active.retrieval_config,
            content_revision=0,
        )

    def verify_generation(
        self,
        identity: RagGenerationIdentity,
        expected_document_version_ids: tuple[int, ...] | None = None,
    ) -> GenerationIntegrityReport:
        """Verify authoritative SQLite plus generation-isolated dense vectors."""
        expected = (
            tuple(
                item.document_version_id for item in self._repository.resolve_rebuild_candidates()
            )
            if expected_document_version_ids is None
            else expected_document_version_ids
        )
        report = self._repository.verify_generation(identity, expected)
        if not report.valid:
            return report
        try:
            indexes = self._repository.list_document_indexes(identity)
            expected_chunk_keys: list[str] = []
            for item in indexes:
                if item.status is DocumentIndexStatus.READY:
                    verification = self._repository.verify_generation_document(
                        identity, item.document_version_id
                    )
                    expected_chunk_keys.extend(verification.chunk_keys)
            dense_count, dense_healthy = self._document_indexer.verify_dense_generation(
                identity, tuple(expected_chunk_keys)
            )
            return replace(
                report,
                dense_vector_count=dense_count,
                dense_index_healthy=dense_healthy,
                valid=report.valid and dense_healthy,
            )
        except RagOperationalError:
            return replace(report, valid=False, dense_index_healthy=False)
        except Exception:
            return replace(report, valid=False, dense_index_healthy=False)

    def recover_building_generations(self) -> tuple[int, ...]:
        """Mark interrupted BUILDING preparations failed under one writer lease."""
        with self._index_lock.acquire():
            recovered: list[int] = []
            for generation in self._repository.list_building_generations():
                self._repository.recover_building_generation(generation.identity)
                recovered.append(generation.generation_id)
            return tuple(recovered)

    def status(self) -> RagIndexStatus:
        """Return safe counts for the sole ACTIVE generation."""
        try:
            active = self._repository.get_active_generation()
        except RagOperationalError as error:
            if error.code is RagErrorCode.NO_ACTIVE_GENERATION:
                return RagIndexStatus(None, 0, 0, 0, 0, False)
            raise
        indexes = self._repository.list_document_indexes(active.identity)
        report = self.verify_generation(active.identity)
        return RagIndexStatus(
            active.identity,
            report.ready_document_count,
            sum(
                item.status is DocumentIndexStatus.READY and item.coverage_status.value == "PARTIAL"
                for item in indexes
            ),
            report.not_searchable_document_count,
            report.chunk_count,
            report.dense_index_healthy,
        )


__all__ = ["DocumentIndexResult", "RagDocumentIndexer", "RagIndexingCoordinator"]
