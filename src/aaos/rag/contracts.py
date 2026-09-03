"""Immutable public contracts for the Phase 4 local RAG boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Protocol, TypeAlias

MAX_RAG_TEXT_CHARACTERS = 16_384
_RAG_ANSWER_VALIDATION_MARKER = object()


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


class SemanticCoverageStatus(StrEnum):
    """Internal model-assisted coverage status, never a public answer status."""

    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


class RequirementSupport(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class AtomicDecompositionStatus(StrEnum):
    OK = "OK"
    TOO_MANY_REQUIREMENTS = "TOO_MANY_REQUIREMENTS"


class EntailmentResult(StrEnum):
    ENTAILED = "ENTAILED"
    NOT_ENTAILED = "NOT_ENTAILED"


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
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(value) > MAX_RAG_TEXT_CHARACTERS:
        raise ValueError(f"{name} must not exceed {MAX_RAG_TEXT_CHARACTERS} characters")
    normalized = value.strip()
    return normalized


def _answer_language(value: object, name: str) -> str:
    normalized = _nonblank_text(value, name).casefold()
    if normalized not in {"en", "ar"}:
        raise ValueError(f"{name} must be 'en' or 'ar'")
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
    # Legacy positional callers may omit this trailing field; selector output
    # always supplies the authoritative persisted count.
    token_count: int = 1

    def __post_init__(self) -> None:
        for name in ("evidence_id", "chunk_key", "exact_text", "document_title"):
            object.__setattr__(self, name, _nonblank_text(getattr(self, name), name))
        if self.heading is not None:
            object.__setattr__(self, "heading", _nonblank_text(self.heading, "heading"))
        for name in ("document_version_id", "course_id", "fused_rank", "generation_id"):
            _positive_int(getattr(self, name), name)
        _nonnegative_int(self.content_revision, "content_revision")
        _positive_int(self.token_count, "token_count")
        if not isinstance(self.source_spans, tuple) or not self.source_spans:
            raise ValueError("source_spans must be a non-empty tuple")
        if not all(isinstance(span, RagChunkSourceSpanValue) for span in self.source_spans):
            raise ValueError("source_spans must contain RAG chunk source spans")
        for name in ("dense_rank", "lexical_rank"):
            rank = getattr(self, name)
            if rank is not None:
                _positive_int(rank, name)
        if self.dense_rank is None and self.lexical_rank is None:
            raise ValueError("at least one retrieval rank is required")


@dataclass(frozen=True, slots=True)
class Requirement:
    """One deterministic, request-local material factual requirement."""

    requirement_id: str
    requirement_text: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.requirement_id, str)
            or len(self.requirement_id) != 2
            or self.requirement_id[0] != "r"
            or self.requirement_id[1] not in "123456"
        ):
            raise ValueError("requirement_id must be r1 through r6")
        object.__setattr__(
            self,
            "requirement_text",
            _nonblank_text(self.requirement_text, "requirement_text"),
        )
        if len(self.requirement_text) > 240:
            raise ValueError("requirement_text must not exceed 240 characters")


@dataclass(frozen=True, slots=True)
class RequirementSet:
    """Immutable canonical requirement universe for one query."""

    requirements: tuple[Requirement, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.requirements, tuple)
            or not self.requirements
            or len(self.requirements) > 6
            or not all(isinstance(item, Requirement) for item in self.requirements)
        ):
            raise ValueError("requirements must contain one through six Requirement values")
        expected = tuple(f"r{index}" for index in range(1, len(self.requirements) + 1))
        actual = tuple(item.requirement_id for item in self.requirements)
        if actual != expected:
            raise ValueError("requirements must have contiguous canonical IDs")


@dataclass(frozen=True, slots=True)
class AtomicRequirementDecomposition:
    """Closed decomposition result, including the non-exceptional overflow refusal."""

    status: AtomicDecompositionStatus
    requirements: tuple[Requirement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, AtomicDecompositionStatus):
            raise ValueError("status must be an AtomicDecompositionStatus")
        if not isinstance(self.requirements, tuple) or not all(
            isinstance(item, Requirement) for item in self.requirements
        ):
            raise ValueError("requirements must be a tuple of Requirement values")
        if self.status is AtomicDecompositionStatus.OK:
            RequirementSet(self.requirements)
        elif self.requirements:
            raise ValueError("overflow decomposition must not expose requirements")


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    evidence_id: str
    quote: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _nonblank_text(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "quote", _nonblank_text(self.quote, "quote"))
        if len(self.evidence_id) > 128 or len(self.quote) > 400:
            raise ValueError("evidence span fields exceed their bounds")


@dataclass(frozen=True, slots=True)
class RequirementEvidenceSpans:
    requirement_id: str
    spans: tuple[EvidenceSpan, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.requirement_id, str) or self.requirement_id not in {
            "r1", "r2", "r3", "r4", "r5", "r6"
        }:
            raise ValueError("requirement_id must be r1 through r6")
        if not isinstance(self.spans, tuple) or len(self.spans) > 4 or not all(
            isinstance(span, EvidenceSpan) for span in self.spans
        ):
            raise ValueError("spans must contain at most four EvidenceSpan values")


@dataclass(frozen=True, slots=True)
class RequirementCoverageJudgment:
    requirement_id: str
    support: RequirementSupport
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.requirement_id, str)
            or len(self.requirement_id) != 2
            or self.requirement_id[0] != "r"
            or self.requirement_id[1] not in "123456"
        ):
            raise ValueError("requirement_id must be r1 through r6")
        if not isinstance(self.support, RequirementSupport):
            raise ValueError("support must be RequirementSupport")
        if not isinstance(self.evidence_ids, tuple) or len(self.evidence_ids) > 8:
            raise ValueError("evidence_ids must be a tuple with at most eight entries")
        normalized = tuple(_nonblank_text(item, "evidence_ids") for item in self.evidence_ids)
        if len(set(normalized)) != len(normalized):
            raise ValueError("evidence_ids must be unique")
        object.__setattr__(self, "evidence_ids", normalized)
        if self.support is RequirementSupport.SUPPORTED and not normalized:
            raise ValueError("SUPPORTED requirements need evidence_ids")
        if self.support is RequirementSupport.UNSUPPORTED and normalized:
            raise ValueError("UNSUPPORTED requirements must have empty evidence_ids")


@dataclass(frozen=True, slots=True)
class RequirementCoverageDecision:
    """Validated coverage judgments and their code-owned aggregate status."""

    requirement_set: RequirementSet
    judgments: tuple[RequirementCoverageJudgment, ...]
    status: SemanticCoverageStatus
    generation_id: int
    content_revision: int
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.requirement_set, RequirementSet):
            raise ValueError("requirement_set must be a RequirementSet")
        if (
            not isinstance(self.judgments, tuple)
            or len(self.judgments) != len(self.requirement_set.requirements)
            or not all(isinstance(item, RequirementCoverageJudgment) for item in self.judgments)
        ):
            raise ValueError("judgments must cover every requirement exactly once")
        expected = tuple(item.requirement_id for item in self.requirement_set.requirements)
        actual = tuple(item.requirement_id for item in self.judgments)
        if actual != expected:
            raise ValueError("judgments must be in canonical requirement order")
        if not isinstance(self.status, SemanticCoverageStatus):
            raise ValueError("status must be SemanticCoverageStatus")
        supported_count = sum(
            item.support is RequirementSupport.SUPPORTED for item in self.judgments
        )
        expected_status = (
            SemanticCoverageStatus.UNSUPPORTED
            if supported_count == 0
            else SemanticCoverageStatus.SUPPORTED
            if supported_count == len(self.judgments)
            else SemanticCoverageStatus.PARTIAL
        )
        if self.status is not expected_status:
            raise ValueError("status must match the deterministic judgment aggregate")
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")
        object.__setattr__(
            self, "policy_version", _nonblank_text(self.policy_version, "policy_version")
        )

    @property
    def supported_requirements(self) -> tuple[Requirement, ...]:
        supported_ids = {
            judgment.requirement_id
            for judgment in self.judgments
            if judgment.support is RequirementSupport.SUPPORTED
        }
        return tuple(
            item
            for item in self.requirement_set.requirements
            if item.requirement_id in supported_ids
        )

    @property
    def unsupported_requirements(self) -> tuple[Requirement, ...]:
        unsupported_ids = {
            judgment.requirement_id
            for judgment in self.judgments
            if judgment.support is RequirementSupport.UNSUPPORTED
        }
        return tuple(
            item
            for item in self.requirement_set.requirements
            if item.requirement_id in unsupported_ids
        )

    @property
    def unsupported_aspects(self) -> tuple[str, ...]:
        return tuple(item.requirement_text for item in self.unsupported_requirements)


# Descriptive aliases keep the public vocabulary stable for callers that use
# the canonical requirement terminology from the design documents.
CanonicalRequirement = Requirement
CanonicalRequirementSet = RequirementSet
RequirementCoverage = RequirementCoverageJudgment


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
class GroundedAnswerRequest:
    """Bounded input supplied to the grounded answer generator."""

    question: str
    answer_language: str
    generation_id: int
    content_revision: int
    evidence: tuple[RetrievedEvidence, ...]
    policy_version: str
    semantic_status: SemanticCoverageStatus | None = None
    unsupported_aspects: tuple[str, ...] = ()
    requirement_coverage: RequirementCoverageDecision | None = None

    @classmethod
    def from_query(
        cls,
        query: RagQuery,
        *,
        generation_id: int,
        content_revision: int,
        evidence: tuple[RetrievedEvidence, ...],
        policy_version: str,
        semantic_decision: SemanticCoverageDecision | None = None,
        requirement_coverage: RequirementCoverageDecision | None = None,
    ) -> "GroundedAnswerRequest":
        """Bind a stateless query to pinned identity and detected language."""
        if not isinstance(query, RagQuery):
            raise ValueError("query must be a RagQuery")
        if semantic_decision is not None and not isinstance(
            semantic_decision, SemanticCoverageDecision
        ):
            raise ValueError("semantic_decision must be a SemanticCoverageDecision or None")
        if semantic_decision is not None and requirement_coverage is not None:
            raise ValueError("semantic_decision and requirement_coverage are mutually exclusive")
        if semantic_decision is not None and (
            semantic_decision.generation_id != generation_id
            or semantic_decision.content_revision != content_revision
            or semantic_decision.policy_version != policy_version
        ):
            raise ValueError("semantic_decision must match the pinned request identity")
        if requirement_coverage is not None and not isinstance(
            requirement_coverage, RequirementCoverageDecision
        ):
            raise ValueError("requirement_coverage must be a RequirementCoverageDecision or None")
        if requirement_coverage is not None and (
            requirement_coverage.generation_id != generation_id
            or requirement_coverage.content_revision != content_revision
            or requirement_coverage.policy_version != policy_version
        ):
            raise ValueError("requirement_coverage must match the pinned request identity")
        answer_language = query.answer_language
        if answer_language is None:
            answer_language = (
                "ar"
                if any("\u0600" <= character <= "\u06ff" for character in query.question)
                else "en"
            )
        return cls(
            question=query.question,
            answer_language=answer_language,
            generation_id=generation_id,
            content_revision=content_revision,
            evidence=evidence,
            policy_version=policy_version,
            semantic_status=(
                requirement_coverage.status
                if requirement_coverage is not None
                else (semantic_decision.status if semantic_decision is not None else None)
            ),
            unsupported_aspects=(
                requirement_coverage.unsupported_aspects
                if requirement_coverage is not None
                else (
                    semantic_decision.unsupported_aspects if semantic_decision is not None else ()
                )
            ),
            requirement_coverage=requirement_coverage,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _nonblank_text(self.question, "question"))
        object.__setattr__(
            self,
            "answer_language",
            _answer_language(self.answer_language, "answer_language"),
        )
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")
        object.__setattr__(
            self,
            "policy_version",
            _nonblank_text(self.policy_version, "policy_version"),
        )
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, RetrievedEvidence) for item in self.evidence
        ):
            raise ValueError("evidence must be a tuple of RetrievedEvidence")
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence IDs must be unique")
        if any(
            item.generation_id != self.generation_id
            or item.content_revision != self.content_revision
            for item in self.evidence
        ):
            raise ValueError("evidence must match the pinned generation and revision")
        object.__setattr__(
            self, "unsupported_aspects", _validate_unsupported_aspects(self.unsupported_aspects)
        )
        if self.semantic_status is not None and not isinstance(
            self.semantic_status, SemanticCoverageStatus
        ):
            raise ValueError("semantic_status must be a SemanticCoverageStatus or None")
        if self.semantic_status is SemanticCoverageStatus.SUPPORTED and self.unsupported_aspects:
            raise ValueError("SUPPORTED semantic coverage must have no unsupported aspects")
        if self.requirement_coverage is not None:
            if not isinstance(self.requirement_coverage, RequirementCoverageDecision):
                raise ValueError(
                    "requirement_coverage must be a RequirementCoverageDecision or None"
                )
            if (
                self.requirement_coverage.generation_id != self.generation_id
                or self.requirement_coverage.content_revision != self.content_revision
                or self.requirement_coverage.policy_version != self.policy_version
            ):
                raise ValueError("requirement_coverage must match the request identity")
            if self.semantic_status is not self.requirement_coverage.status:
                raise ValueError("semantic_status must match requirement_coverage")
            request_evidence_ids = frozenset(evidence_ids)
            for judgment in self.requirement_coverage.judgments:
                if any(
                    evidence_id not in request_evidence_ids for evidence_id in judgment.evidence_ids
                ):
                    raise ValueError("requirement coverage evidence must be request-local")
            if self.unsupported_aspects != self.requirement_coverage.unsupported_aspects:
                raise ValueError("unsupported_aspects must match requirement_coverage")


@dataclass(frozen=True, slots=True)
class SemanticCoverageDecision:
    """Request-bound, advisory semantic coverage classification."""

    status: SemanticCoverageStatus
    unsupported_aspects: tuple[str, ...]
    generation_id: int
    content_revision: int
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SemanticCoverageStatus):
            raise ValueError("status must be a SemanticCoverageStatus")
        object.__setattr__(
            self, "unsupported_aspects", _validate_unsupported_aspects(self.unsupported_aspects)
        )
        if self.status is SemanticCoverageStatus.SUPPORTED and self.unsupported_aspects:
            raise ValueError("SUPPORTED semantic coverage must have no unsupported aspects")
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")
        object.__setattr__(
            self, "policy_version", _nonblank_text(self.policy_version, "policy_version")
        )


# The classifier consumes the same bounded, request-local data as generation.
# Keep this alias for callers that want to name that boundary explicitly.
SemanticCoverageRequest = GroundedAnswerRequest


def _validate_unsupported_aspects(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > 4:
        raise ValueError("unsupported_aspects must be a tuple with at most four entries")
    normalized: list[str] = []
    for item in value:
        normalized.append(_nonblank_text(item, "unsupported_aspects"))
    if len(set(normalized)) != len(normalized):
        raise ValueError("unsupported_aspects must be unique")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class GroundedModelOutput:
    """Strict model output before Task 18 citation validation/rendering."""

    status: RagAnswerStatus
    answer_text: str
    language: str
    claims: tuple[RagClaim, ...]
    generation_id: int
    content_revision: int
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, RagAnswerStatus):
            raise ValueError("status must be a RagAnswerStatus")
        object.__setattr__(self, "answer_text", _nonblank_text(self.answer_text, "answer_text"))
        object.__setattr__(self, "language", _answer_language(self.language, "language"))
        if not isinstance(self.claims, tuple) or not all(
            isinstance(claim, RagClaim) for claim in self.claims
        ):
            raise ValueError("claims must be a tuple of RagClaim")
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")
        object.__setattr__(
            self,
            "policy_version",
            _nonblank_text(self.policy_version, "policy_version"),
        )
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim IDs must be unique")
        if self.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            if self.claims:
                raise ValueError("insufficient output must not contain claims")
        elif not self.claims:
            raise ValueError("answer output must contain claims")


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
    _validation_marker: object = field(default=None, init=False, repr=False, compare=False)

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
        claim_ids = {claim.claim_id for claim in self.claims}
        citation_claim_ids = {citation.claim_id for citation in self.citations}
        if self.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            if self.claims or self.citations:
                raise ValueError("insufficient answer must not contain claims or citations")
        elif not self.claims or citation_claim_ids != claim_ids:
            raise ValueError("every answer claim must have a citation")
        _positive_int(self.generation_id, "generation_id")
        _nonnegative_int(self.content_revision, "content_revision")


@dataclass(frozen=True, slots=True)
class RenderedRagResponse:
    answer: RagAnswer
    rendered_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.answer, RagAnswer):
            raise ValueError("answer must be a RagAnswer")
        if self.answer._validation_marker is not _RAG_ANSWER_VALIDATION_MARKER:
            raise ValueError("answer must pass citation validation")
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
