"""Lease-protected, per-document cross-store RAG indexing orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from aaos.rag.chunking import DeterministicChunker
from aaos.rag.contracts import (
    CoverageStatus,
    DenseIndexFactory,
    DenseVectorRecord,
    DocumentIndexStatus,
    EmbeddingService,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)
from aaos.rag.lock import RagIndexLock
from aaos.rag.repository import DocumentVerificationRecord, RagRepository


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
        self, generation: RagGenerationIdentity, document_version_id: int
    ) -> DocumentIndexResult:
        """Persist, vectorize, and verify one document without making it query-visible."""
        with self._index_lock.acquire():
            return self._prepare_locked(generation, document_version_id)

    def _prepare_locked(
        self, generation: RagGenerationIdentity, document_version_id: int
    ) -> DocumentIndexResult:
        preparing_started = False
        try:
            build = self._repository.get_generation(generation.generation_id)
            metadata = self._embedding_service.model_metadata()
            if (
                build.identity != generation
                or build.status.value != "BUILDING"
                or self._chunker.config_fingerprint != generation.config_fingerprint
                or metadata.model_identifier != build.embedding_model
                or metadata.model_revision != build.embedding_revision
                or metadata.dimensions != generation.embedding_dimensions
            ):
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            version = self._repository.get_eligible_document_version(document_version_id)
            pages = self._repository.read_pages(document_version_id)
            self._repository.begin_document_preparation(
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
                self._repository.finish_document_preparation(
                    generation,
                    expected_revision=generation.content_revision,
                    document_version_id=document_version_id,
                    status=DocumentIndexStatus.NOT_SEARCHABLE,
                    coverage_status=coverage,
                    searchable_page_count=0,
                    chunk_count=0,
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
            for draft in chunks:
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
                for span_ordinal, span in enumerate(draft.source_spans, start=1):
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
                vectors = self._embedding_service.embed_documents(
                    tuple(chunk.exact_text for chunk in chunks)
                )
            except RagOperationalError:
                raise
            except Exception as error:
                raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED) from error
            self._validate_vectors(vectors, chunks, generation.embedding_dimensions)
            try:
                dense = self._dense_factory.open(generation)
                try:
                    dense.upsert(
                        tuple(
                            DenseVectorRecord(chunk.chunk_key, document_version_id, vector)
                            for chunk, vector in zip(chunks, vectors, strict=True)
                        )
                    )
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


__all__ = ["DocumentIndexResult", "RagDocumentIndexer"]
