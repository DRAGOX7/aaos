from __future__ import annotations

from dataclasses import fields

import pytest

import aaos.rag.contracts as contracts
from aaos.rag.contracts import (
    RagAnswerStatus,
    RagChunkStructureSourceSpanValue,
    RagClaim,
    RetrievedEvidence,
)
from aaos.rag.generation import (
    CitationValidator,
    GroundedModelOutput,
    render_rag_answer,
)


def _structural_evidence() -> RetrievedEvidence:
    text = "Neural Networks"

    span = RagChunkStructureSourceSpanValue(
        document_structure_unit_id=17,
        unit_ordinal=4,
        kind="HEADING",
        heading_level=2,
        table_index=None,
        row_index=None,
        column_index=None,
        source_char_start=0,
        source_char_end=len(text),
        chunk_char_start=0,
        chunk_char_end=len(text),
    )

    return RetrievedEvidence(
        evidence_id="ev-struct",
        chunk_key="structural-chunk",
        exact_text=text,
        heading="Neural Networks",
        document_version_id=101,
        document_title="notes.docx",
        course_id=11,
        source_spans=(span,),
        dense_rank=1,
        lexical_rank=None,
        fused_rank=1,
        generation_id=3,
        content_revision=7,
        token_count=2,
        provenance_mode="STRUCTURE",
    )


def _output() -> GroundedModelOutput:
    return GroundedModelOutput(
        status=RagAnswerStatus.ANSWER,
        answer_text="Neural networks are covered.",
        language="en",
        claims=(
            RagClaim(
                "claim-1",
                "Neural networks are covered.",
                ("ev-struct",),
            ),
        ),
        generation_id=3,
        content_revision=7,
        policy_version="grounded-v1",
    )


def test_structural_citation_contract_is_page_free() -> None:
    citation_type = getattr(
        contracts,
        "StructuralCitation",
        None,
    )

    assert citation_type is not None

    names = {
        field.name
        for field in fields(citation_type)
    }

    assert names == {
        "claim_id",
        "document_version_id",
        "document_title",
        "unit_start",
        "unit_end",
        "heading",
        "table_index",
        "row_index",
        "column_index",
        "provenance_kind",
    }

    assert "page_start" not in names
    assert "page_end" not in names


def test_page_and_structural_citation_kinds_cannot_mix() -> None:
    with pytest.raises(ValueError):
        contracts.Citation(
            "claim-1",
            101,
            "source.pdf",
            1,
            1,
            provenance_kind=(
                contracts.ProvenanceKind.STRUCTURAL_DOCUMENT
            ),
        )

    with pytest.raises(ValueError):
        contracts.StructuralCitation(
            claim_id="claim-1",
            document_version_id=101,
            document_title="notes.docx",
            unit_start=1,
            unit_end=1,
            heading=None,
            table_index=None,
            row_index=None,
            column_index=None,
            provenance_kind=(
                contracts.ProvenanceKind.EMBEDDED_PDF
            ),
        )


def test_validator_maps_structural_evidence_without_fake_pages() -> None:
    evidence = _structural_evidence()

    answer = CitationValidator().validate(
        _output(),
        (evidence,),
    )

    assert len(answer.citations) == 1

    citation = answer.citations[0]

    structural_type = getattr(
        contracts,
        "StructuralCitation",
        None,
    )

    assert structural_type is not None
    assert isinstance(citation, structural_type)

    assert citation.claim_id == "claim-1"
    assert citation.document_version_id == 101
    assert citation.document_title == "notes.docx"

    assert citation.unit_start == 4
    assert citation.unit_end == 4
    assert citation.heading == "Neural Networks"

    assert citation.table_index is None
    assert citation.row_index is None
    assert citation.column_index is None
    assert (
        citation.provenance_kind
        is contracts.ProvenanceKind.STRUCTURAL_DOCUMENT
    )

    assert not hasattr(citation, "page_start")
    assert not hasattr(citation, "page_end")


def test_renderer_uses_structural_location_not_page_label() -> None:
    answer = CitationValidator().validate(
        _output(),
        (_structural_evidence(),),
    )

    rendered = render_rag_answer(answer)

    assert rendered.rendered_text == (
        "Neural networks are covered.\n\n"
        "[claim-1] notes.docx "
        "(unit 4; heading Neural Networks)"
    )

    assert "page " not in rendered.rendered_text
    assert "pages " not in rendered.rendered_text
