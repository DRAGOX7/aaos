"""Tests for the Phase 4 public RAG contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from aaos.rag.contracts import (
    Citation,
    CourseScope,
    CoverageStatus,
    DenseSearchHit,
    DenseVectorRecord,
    DocumentIndexStatus,
    EmbeddingModelMetadata,
    GenerationStatus,
    GlobalScope,
    HistoricalScope,
    RagAnswer,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagClaim,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
    RagQuery,
    RetrievedEvidence,
)


def test_closed_enums_expose_only_approved_values() -> None:
    assert {member.value for member in GenerationStatus} == {"BUILDING", "ACTIVE", "RETIRED"}
    assert {member.value for member in DocumentIndexStatus} == {
        "PREPARING",
        "READY",
        "NOT_SEARCHABLE",
        "FAILED",
    }
    assert {member.value for member in CoverageStatus} == {"FULL", "PARTIAL", "NONE"}
    assert {member.value for member in RagAnswerStatus} == {
        "ANSWER",
        "PARTIAL_ANSWER",
        "INSUFFICIENT_EVIDENCE",
    }
    assert {member.value for member in RagErrorCode} == {
        "RAG_INDEX_LOCKED",
        "NO_ELIGIBLE_DOCUMENTS",
        "DOCUMENT_INDEX_FAILED",
        "EMBEDDING_FAILED",
        "DENSE_INDEX_WRITE_FAILED",
        "FTS_INDEX_FAILED",
        "INDEX_INTEGRITY_FAILED",
        "GENERATION_ACTIVATION_FAILED",
        "NO_ACTIVE_GENERATION",
        "INVALID_QUERY_SCOPE",
        "QUERY_EMBEDDING_FAILED",
        "DENSE_INDEX_UNAVAILABLE",
        "LEXICAL_INDEX_FAILED",
        "RETRIEVAL_INCONSISTENCY",
        "INSUFFICIENT_EVIDENCE",
        "MODEL_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "INVALID_STRUCTURED_OUTPUT",
        "INVALID_EVIDENCE_REFERENCE",
        "UNCITED_CLAIM",
        "PROVENANCE_RESOLUTION_FAILED",
    }


def test_public_records_are_validated_immutable_and_slot_backed() -> None:
    course_scope = CourseScope(course_id=1)
    global_scope = GlobalScope()
    historical_scope = HistoricalScope(document_version_ids=(1, 2))
    query = RagQuery(question="What is the deadline?", scope=course_scope, answer_language="en")
    generation = RagGenerationIdentity(1, "a" * 64, 0, 1024)
    metadata = EmbeddingModelMetadata("BAAI/bge-m3", "v1", 1024, "cuda")
    vector = DenseVectorRecord("chunk-1", 1, (0.1, 0.2))
    hit = DenseSearchHit("chunk-1", 1, 0.2)
    span = RagChunkSourceSpanValue(1, 1, 0, 10, 0, 10)
    evidence = RetrievedEvidence(
        "evidence-1", "chunk-1", "Evidence text", None, 1, "Lecture", 1, (span,), 1, None, 1, 1, 0
    )
    claim = RagClaim("claim-1", "The deadline is Friday.", ("evidence-1",))
    citation = Citation("claim-1", 1, "Lecture", 1, 1)
    answer = RagAnswer(
        RagAnswerStatus.ANSWER, "The deadline is Friday.", "en", (claim,), (citation,), 1, 0
    )

    for record in (
        course_scope,
        global_scope,
        historical_scope,
        query,
        generation,
        metadata,
        vector,
        hit,
        span,
        evidence,
        claim,
        citation,
        answer,
    ):
        assert not hasattr(record, "__dict__")

    with pytest.raises(FrozenInstanceError):
        query.question = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: CourseScope(0), "course_id"),
        (lambda: HistoricalScope(()), "document_version_ids"),
        (lambda: HistoricalScope((1, 0)), "document_version_ids"),
        (lambda: RagQuery("   ", GlobalScope()), "question"),
        (lambda: RagQuery("question", GlobalScope(), " "), "answer_language"),
        (lambda: RagGenerationIdentity(0, "a" * 64, 0, 1), "generation_id"),
        (lambda: RagGenerationIdentity(1, "not-a-hash", 0, 1), "config_fingerprint"),
        (lambda: EmbeddingModelMetadata("model", "revision", 0, "cpu"), "dimensions"),
        (lambda: DenseVectorRecord("chunk", 1, (nan,)), "vector"),
        (lambda: DenseVectorRecord("chunk", 1, (inf,)), "vector"),
        (lambda: DenseSearchHit("chunk", 0, 0.1), "rank"),
        (lambda: RagChunkSourceSpanValue(1, 1, 3, 2, 0, 1), "source_char"),
        (lambda: RagClaim("claim", "text", ()), "evidence_ids"),
        (lambda: Citation("claim", 1, "title", 2, 1), "page"),
    ],
)
def test_invalid_contract_values_are_rejected(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()  # type: ignore[operator]


def test_safe_operational_error_contains_only_fixed_code_and_message() -> None:
    error = RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)

    assert error.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert str(error) == "rag operation failed"
