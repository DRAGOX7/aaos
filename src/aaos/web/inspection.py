"""Project optional validated RAG metadata without changing the agent contract."""

from __future__ import annotations

from aaos.rag.inspection import InspectionRecord
from aaos.web.contracts import (
    AcademicAskRagAnswerProvenanceView,
    AcademicAskRagProvenanceSummaryView,
    CitationInspectionView,
    CitationPassageView,
)


def enrich_inspection(
    provenance: AcademicAskRagAnswerProvenanceView | AcademicAskRagProvenanceSummaryView | None,
    records: list[InspectionRecord],
) -> AcademicAskRagAnswerProvenanceView | AcademicAskRagProvenanceSummaryView | None:
    if not isinstance(provenance, AcademicAskRagAnswerProvenanceView):
        return provenance
    matches = [
        record
        for record in records
        if (
            record.response.answer.generation_id == provenance.generation_id
            and record.response.answer.content_revision == provenance.content_revision
            and record.response.answer.answer_text == provenance.answer_text
            and record.response.rendered_text == provenance.rendered_text
            and record.response.answer.status.value == provenance.status
            and record.response.answer.language == provenance.language
        )
    ]
    if len(matches) != 1:
        return provenance
    citations = []
    for citation in provenance.citations:
        matches_for_citation = []
        public = citation.model_dump(exclude={"inspection"})
        for trusted, claim_text, spans in matches[0].bindings:
            if all(getattr(trusted, key, None) == value for key, value in public.items()):
                matches_for_citation.append((claim_text, spans))
        if len(matches_for_citation) == 1:
            claim, spans = matches_for_citation[0]
            inspection = CitationInspectionView(
                document_version_id=citation.document_version_id,
                claim_text=claim,
                passages=tuple(
                    CitationPassageView(location=s.location, start=s.start, end=s.end, text=s.text)
                    for s in spans
                ),
            )
            citation = citation.model_copy(update={"inspection": inspection})
        citations.append(citation)
    return provenance.model_copy(update={"citations": tuple(citations)})
