"""Trusted one-way projection from visual analysis to existing RAG contracts."""

from __future__ import annotations

from aaos.rag.contracts import (
    MultimodalEvidence,
    RagAnswerStatus,
    RagClaim,
    RenderedRagResponse,
)
from aaos.rag.generation import CitationValidator

from .visual_provenance import VisualAnalysis, VisualStatus


def project_visual_elements(
    analysis: VisualAnalysis,
    *,
    analysis_run_id: int,
    visual_element_ids: tuple[int, ...],
    document_title: str,
    course_id: int,
    generation_id: int,
    content_revision: int,
) -> tuple[MultimodalEvidence, ...]:
    """Project validated visual elements as derived evidence, never PDF spans."""

    if not isinstance(analysis, VisualAnalysis):
        raise TypeError("analysis must be a VisualAnalysis")
    if analysis.status not in {VisualStatus.SUCCEEDED, VisualStatus.PARTIAL}:
        raise ValueError("only usable visual analysis can be projected")
    if not isinstance(visual_element_ids, tuple) or len(visual_element_ids) != len(
        analysis.elements
    ):
        raise ValueError("visual element IDs must match the analysis elements")
    if not isinstance(document_title, str) or not document_title.strip():
        raise ValueError("document_title must be non-blank")
    if isinstance(course_id, bool) or not isinstance(course_id, int) or course_id <= 0:
        raise ValueError("course_id must be positive")
    if (
        isinstance(analysis_run_id, bool)
        or not isinstance(analysis_run_id, int)
        or analysis_run_id <= 0
    ):
        raise ValueError("analysis_run_id must be positive")
    projected: list[MultimodalEvidence] = []
    for sequence, (element_id, element) in enumerate(
        zip(visual_element_ids, analysis.elements, strict=True), start=1
    ):
        if isinstance(element_id, bool) or not isinstance(element_id, int) or element_id <= 0:
            raise ValueError("visual element IDs must be positive")
        representations = tuple(
            value
            for value in (
                element.observed_text,
                element.interpretation_text,
                element.structured_payload,
            )
            if value is not None
        )
        derived_text = "\n".join(representations)
        if derived_text is None:
            raise ValueError("visual element has no bounded derived representation")
        projected.append(
            MultimodalEvidence(
                evidence_id=f"visual-{analysis_run_id}-{sequence}",
                derived_text=derived_text,
                document_version_id=analysis.document_version_id,
                document_title=document_title,
                page_number=analysis.page_number,
                course_id=course_id,
                analysis_run_id=analysis_run_id,
                visual_element_id=element_id,
                source_content_sha256=analysis.source_content_sha256,
                render_sha256=analysis.render_sha256,
                model_id=analysis.model_id,
                model_revision=analysis.model_revision,
                prompt_revision=analysis.prompt_revision,
                analysis_schema_revision=analysis.analysis_schema_revision,
                generation_id=generation_id,
                content_revision=content_revision,
            )
        )
    return tuple(projected)


def project_multimodal_answer(
    *,
    status: VisualStatus,
    answer_text: str | None,
    language: str,
    evidence: tuple[MultimodalEvidence, ...],
    generation_id: int,
    content_revision: int,
) -> RenderedRagResponse:
    """Render an answer only after trusted derived-visual citation validation."""

    status = status if isinstance(status, VisualStatus) else VisualStatus(status)
    if language not in {"en", "ar"}:
        raise ValueError("language must be en or ar")
    if status in {VisualStatus.FAILED, VisualStatus.TIMED_OUT, VisualStatus.UNAVAILABLE}:
        if answer_text is not None or evidence:
            raise ValueError("failed visual analysis cannot produce grounded evidence")
        rag_status = RagAnswerStatus.INSUFFICIENT_EVIDENCE
        claims: tuple[RagClaim, ...] = ()
    else:
        if answer_text is None or not answer_text.strip() or not evidence:
            raise ValueError("usable visual answer requires bounded evidence")
        rag_status = (
            RagAnswerStatus.PARTIAL_ANSWER
            if status is VisualStatus.PARTIAL
            else RagAnswerStatus.ANSWER
        )
        claims = (
            RagClaim("visual-claim-1", answer_text, tuple(item.evidence_id for item in evidence)),
        )
    answer = CitationValidator().validate_multimodal(
        claims=claims,
        evidence=evidence,
        language=language,
        status=rag_status,
        generation_id=generation_id,
        content_revision=content_revision,
    )
    from aaos.rag.generation import render_rag_answer

    return render_rag_answer(answer)


__all__ = [
    "MultimodalEvidence",
    "project_multimodal_answer",
    "project_visual_elements",
]
