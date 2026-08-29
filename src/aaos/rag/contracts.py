"""Immutable public contracts for the Phase 4 local RAG boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, TypeAlias


class GenerationStatus(StrEnum):
    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class DocumentIndexStatus(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    NOT_SEARCHABLE = "NOT_SEARCHABLE"
    FAILED = "FAILED"


class CoverageStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class RagAnswerStatus(StrEnum):
    ANSWER = "ANSWER"
    PARTIAL_ANSWER = "PARTIAL_ANSWER"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RagErrorCode(StrEnum):
    RAG_INDEX_LOCKED = "RAG_INDEX_LOCKED"
    NO_ELIGIBLE_DOCUMENTS = "NO_ELIGIBLE_DOCUMENTS"
    DOCUMENT_INDEX_FAILED = "DOCUMENT_INDEX_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    DENSE_INDEX_WRITE_FAILED = "DENSE_INDEX_WRITE_FAILED"
    FTS_INDEX_FAILED = "FTS_INDEX_FAILED"
    INDEX_INTEGRITY_FAILED = "INDEX_INTEGRITY_FAILED"
    GENERATION_ACTIVATION_FAILED = "GENERATION_ACTIVATION_FAILED"
    NO_ACTIVE_GENERATION = "NO_ACTIVE_GENERATION"
    INVALID_QUERY_SCOPE = "INVALID_QUERY_SCOPE"
    QUERY_EMBEDDING_FAILED = "QUERY_EMBEDDING_FAILED"
    DENSE_INDEX_UNAVAILABLE = "DENSE_INDEX_UNAVAILABLE"
    LEXICAL_INDEX_FAILED = "LEXICAL_INDEX_FAILED"
    RETRIEVAL_INCONSISTENCY = "RETRIEVAL_INCONSISTENCY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    INVALID_STRUCTURED_OUTPUT = "INVALID_STRUCTURED_OUTPUT"
    INVALID_EVIDENCE_REFERENCE = "INVALID_EVIDENCE_REFERENCE"
    UNCITED_CLAIM = "UNCITED_CLAIM"
    PROVENANCE_RESOLUTION_FAILED = "PROVENANCE_RESOLUTION_FAILED"


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _nonblank_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{name} must be a non-blank string")
    return normalized


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True, slots=True)
class CourseScope:
    course_id: int

    def __post_init__(self) -> None:
        _positive_int(self.course_id, "course_id")


@dataclass(frozen=True, slots=True)
class GlobalScope:
    pass


@dataclass(frozen=True, slots=True)
class HistoricalScope:
    document_version_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.document_version_ids, tuple) or not self.document_version_ids:
            raise ValueError("document_version_ids must be a non-empty tuple")
        for document_version_id in self.document_version_ids:
            _positive_int(document_version_id, "document_version_ids")


RagScope: TypeAlias = CourseScope | GlobalScope | HistoricalScope


@dataclass(frozen=True, slots=True)
class RagQuery:
    question: str
    scope: RagScope
    answer_language: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _nonblank_text(self.question, "question"))
        if not isinstance(self.scope, (CourseScope, GlobalScope, HistoricalScope)):
            raise ValueError("scope must be a supported RAG scope")
        if self.answer_language is not None:
            object.__setattr__(
                self, "answer_language", _nonblank_text(self.answer_language, "answer_language")
            )


@dataclass(frozen=True, slots=True)
class RagGenerationIdentity:
    generation_id: int
    config_fingerprint: str
    content_revision: int
    embedding_dimensions: int

    def __post_init__(self) -> None:
        _positive_int(self.generation_id, "generation_id")
        if (
            not isinstance(self.config_fingerprint, str)
            or len(self.config_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.config_fingerprint)
        ):
            raise ValueError("config_fingerprint must be a lowercase SHA-256 hexadecimal digest")
        _nonnegative_int(self.content_revision, "content_revision")
        _positive_int(self.embedding_dimensions, "embedding_dimensions")


@dataclass(frozen=True, slots=True)
class EmbeddingModelMetadata:
    model_identifier: str
    model_revision: str
    dimensions: int
    device: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "model_identifier", _nonblank_text(self.model_identifier, "model_identifier")
        )
        object.__setattr__(
            self, "model_revision", _nonblank_text(self.model_revision, "model_revision")
        )
        _positive_int(self.dimensions, "dimensions")
        object.__setattr__(self, "device", _nonblank_text(self.device, "device"))


@dataclass(frozen=True, slots=True)
class DenseVectorRecord:
    chunk_key: str
    document_version_id: int
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_key", _nonblank_text(self.chunk_key, "chunk_key"))
        _positive_int(self.document_version_id, "document_version_id")
        if not isinstance(self.vector, tuple) or not self.vector:
            raise ValueError("vector must be a non-empty tuple")
        for value in self.vector:
            _finite_float(value, "vector")


@dataclass(frozen=True, slots=True)
class DenseSearchHit:
    chunk_key: str
    rank: int
    distance: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_key", _nonblank_text(self.chunk_key, "chunk_key"))
        _positive_int(self.rank, "rank")
        _finite_float(self.distance, "distance")


class EmbeddingService(Protocol):
    def model_metadata(self) -> EmbeddingModelMetadata: ...

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...

    def release_model(self) -> None: ...

    def close(self) -> None: ...


class DenseIndex(Protocol):
    def upsert(self, records: tuple[DenseVectorRecord, ...]) -> None: ...

    def search(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
        allowed_document_version_ids: tuple[int, ...],
    ) -> tuple[DenseSearchHit, ...]: ...

    def contains(self, chunk_keys: tuple[str, ...]) -> bool: ...

    def count(self) -> int: ...

    def close(self) -> None: ...


class DenseIndexFactory(Protocol):
    def open(self, generation: RagGenerationIdentity) -> DenseIndex: ...

    def remove(self, generation: RagGenerationIdentity) -> None: ...


@dataclass(frozen=True, slots=True)
class RagChunkSourceSpanValue:
    document_page_id: int
    page_number: int
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int

    def __post_init__(self) -> None:
        _positive_int(self.document_page_id, "document_page_id")
        _positive_int(self.page_number, "page_number")
        for name in ("source_char_start", "source_char_end", "chunk_char_start", "chunk_char_end"):
            _nonnegative_int(getattr(self, name), name)
        if self.source_char_end <= self.source_char_start:
            raise ValueError("source_char span must be monotonic and non-empty")
        if self.chunk_char_end <= self.chunk_char_start:
            raise ValueError("chunk_char span must be monotonic and non-empty")


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    evidence_id: str
    chunk_key: str
    exact_text: str
    heading: str | None
    document_version_id: int
    document_title: str
    course_id: int
    source_spans: tuple[RagChunkSourceSpanValue, ...]
    dense_rank: int | None
    lexical_rank: int | None
    fused_rank: int
    generation_id: int
    content_revision: int

    def __post_init__(self) -> None:
        for name in ("evidence_id", "chunk_key", "exact_text", "document_title"):
            object.__setattr__(self, name, _nonblank_text(getattr(self, name), name))
        if self.heading is not None:
            object.__setattr__(self, "heading", _nonblank_text(self.heading, "heading"))
        for name in ("document_version_id", "course_id", "fused_rank", "generation_id"):
            _positive_int(getattr(self, name), name)
        _nonnegative_int(self.content_revision, "content_revision")
        if not isinstance(self.source_spans, tuple) or not self.source_spans:
            raise ValueError("source_spans must be a non-empty tuple")
        if not all(isinstance(span, RagChunkSourceSpanValue) for span in self.source_spans):
            raise ValueError("source_spans must contain RAG chunk source spans")
        for name in ("dense_rank", "lexical_rank"):
            rank = getattr(self, name)
            if rank is not None:
                _positive_int(rank, name)


@dataclass(frozen=True, slots=True)
class RagClaim:
    claim_id: str
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _nonblank_text(self.claim_id, "claim_id"))
        object.__setattr__(self, "text", _nonblank_text(self.text, "text"))
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise ValueError("evidence_ids must be a non-empty tuple")
        for evidence_id in self.evidence_ids:
            _nonblank_text(evidence_id, "evidence_ids")


@dataclass(frozen=True, slots=True)
class Citation:
    claim_id: str
    document_version_id: int
    document_title: str
    page_start: int
    page_end: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _nonblank_text(self.claim_id, "claim_id"))
        _positive_int(self.document_version_id, "document_version_id")
        object.__setattr__(
            self, "document_title", _nonblank_text(self.document_title, "document_title")
        )
        _positive_int(self.page_start, "page_start")
        _positive_int(self.page_end, "page_end")
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")


@dataclass(frozen=True, slots=True)
class RagAnswer:
    status: RagAnswerStatus
    answer_text: str
    language: str
    claims: tuple[RagClaim, ...]
    citations: tuple[Citation, ...]
    generation_id: int
    content_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, RagAnswerStatus):
            raise ValueError("status must be a RagAnswerStatus")
        object.__setattr__(self, "answer_text", _nonblank_text(self.answer_text, "answer_text"))
        object.__setattr__(self, "language", _nonblank_text(self.language, "language"))
        if not isinstance(self.claims, tuple) or not all(
            isinstance(claim, RagClaim) for claim in self.claims
        ):
            raise ValueError("claims must be a tuple of RagClaim")
        if not isinstance(self.citations, tuple) or not all(
            isinstance(citation, Citation) for citation in self.citations
        ):
            raise ValueError("citations must be a tuple of Citation")
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")


@dataclass(frozen=True, slots=True)
class RenderedRagResponse:
    answer: RagAnswer
    rendered_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.answer, RagAnswer):
            raise ValueError("answer must be a RagAnswer")
        object.__setattr__(
            self, "rendered_text", _nonblank_text(self.rendered_text, "rendered_text")
        )


@dataclass(frozen=True, slots=True)
class IndexRunResult:
    safe_status: str
    generation_id: int
    content_revision: int
    indexed_documents: int
    not_searchable_documents: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "safe_status", _nonblank_text(self.safe_status, "safe_status"))
        _positive_int(self.generation_id, "generation_id")
        for name in ("content_revision", "indexed_documents", "not_searchable_documents"):
            _nonnegative_int(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class RagIndexStatus:
    active_generation: RagGenerationIdentity | None
    ready_documents: int
    partial_documents: int
    not_searchable_documents: int
    chunks: int
    dense_index_healthy: bool

    def __post_init__(self) -> None:
        if self.active_generation is not None and not isinstance(
            self.active_generation, RagGenerationIdentity
        ):
            raise ValueError("active_generation must be a RagGenerationIdentity or None")
        for name in ("ready_documents", "partial_documents", "not_searchable_documents", "chunks"):
            _nonnegative_int(getattr(self, name), name)
        if not isinstance(self.dense_index_healthy, bool):
            raise ValueError("dense_index_healthy must be a bool")


class LocalRagService(Protocol):
    async def ask(self, query: RagQuery) -> RenderedRagResponse: ...


class RagIndexingService(Protocol):
    def index_new(self, document_version_id: int | None = None) -> IndexRunResult: ...

    def rebuild(self) -> IndexRunResult: ...

    def status(self) -> RagIndexStatus: ...


class RagOperationalError(RuntimeError):
    """Internal fixed-message error carrying only a safe RAG error code."""

    def __init__(self, code: RagErrorCode) -> None:
        if not isinstance(code, RagErrorCode):
            raise ValueError("code must be a RagErrorCode")
        super().__init__("rag operation failed")
        self.code = code
