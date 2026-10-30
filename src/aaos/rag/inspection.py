"""Optional request-local UI provenance, recorded only after answer validation.

This side channel never enters the agent/model observation schema or influences
verification. It identifies retrieved source spans, not minimal NLI premises.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from aaos.rag.contracts import (
    Citation,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RenderedRagResponse,
    RetrievedEvidence,
    StructuralCitation,
)


@dataclass(frozen=True)
class InspectionSpan:
    location: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class InspectionRecord:
    response: RenderedRagResponse
    # Full trusted citation equality prevents accidental cross-document/claim joins.
    bindings: tuple[tuple[Citation | StructuralCitation, str, tuple[InspectionSpan, ...]], ...]


INSPECTIONS: ContextVar[list[InspectionRecord] | None] = ContextVar("rag_inspections", default=None)


def record_inspection(
    response: RenderedRagResponse, evidence: tuple[RetrievedEvidence, ...]
) -> None:
    records = INSPECTIONS.get()
    if records is None or response.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
        return
    bindings = []
    remaining = 32000
    for citation in response.answer.citations:
        claim = next(
            (item for item in response.answer.claims if item.claim_id == citation.claim_id), None
        )
        if claim is None or len(claim.text) > 4096:
            continue
        matching = []
        for item in evidence:
            if (
                item.evidence_id not in claim.evidence_ids
                or item.document_version_id != citation.document_version_id
                or item.generation_id != response.answer.generation_id
                or item.content_revision != response.answer.content_revision
            ):
                continue
            locations = [
                span.page_number if isinstance(span, RagChunkSourceSpanValue) else span.unit_ordinal
                for span in item.source_spans
            ]
            expected = (
                (citation.page_start, citation.page_end)
                if isinstance(citation, Citation)
                else (citation.unit_start, citation.unit_end)
            )
            if (min(locations), max(locations)) != expected:
                continue
            matching.append(item)
        # A page-level collision is ambiguous: do not guess which chunk a citation meant.
        if len(matching) != 1 or len(claim.text) > remaining:
            continue
        remaining -= len(claim.text)
        spans: list[InspectionSpan] = []
        for span in matching[0].source_spans:
            text = matching[0].exact_text[span.chunk_char_start : span.chunk_char_end]
            if not text or len(text) > min(remaining, 16000) or len(spans) >= 4:
                break
            spans.append(
                InspectionSpan(
                    span.page_number
                    if isinstance(span, RagChunkSourceSpanValue)
                    else span.unit_ordinal,
                    span.source_char_start,
                    span.source_char_end,
                    text,
                )
            )
            remaining -= len(text)
        if spans:
            bindings.append((citation, claim.text, tuple(spans)))
    records.append(InspectionRecord(response, tuple(bindings)))
    # Multiple RAG calls in an agent run remain request-local and bounded.
    del records[:-4]
