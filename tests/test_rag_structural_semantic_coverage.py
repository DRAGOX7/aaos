from __future__ import annotations

import pytest

from aaos.rag.contracts import (
    RagChunkStructureSourceSpanValue,
    RagOperationalError,
    Requirement,
    RequirementSet,
    RetrievedEvidence,
)
from aaos.rag.semantic_coverage import (
    _validate_coverage_inputs,
    _validate_span_inputs,
)


def _requirements() -> RequirementSet:
    return RequirementSet(
        (
            Requirement(
                "r1",
                "Explain neural networks.",
            ),
        )
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


def test_coverage_validation_accepts_structural_provenance() -> None:
    evidence = _structural_evidence()

    _validate_coverage_inputs(
        _requirements(),
        "Explain neural networks.",
        "en",
        3,
        7,
        "grounded-v1",
        (evidence,),
    )

    span = evidence.source_spans[0]

    assert not hasattr(
        span,
        "page_number",
    )
    assert not hasattr(
        span,
        "document_page_id",
    )


def test_span_validation_accepts_structural_provenance() -> None:
    evidence = _structural_evidence()

    _validate_span_inputs(
        _requirements(),
        (evidence,),
    )

    assert evidence.provenance_mode == "STRUCTURE"


def test_coverage_validation_rejects_wrong_span_family() -> None:
    evidence = _structural_evidence()

    object.__setattr__(
        evidence,
        "provenance_mode",
        "PAGE",
    )

    with pytest.raises(RagOperationalError):
        _validate_coverage_inputs(
            _requirements(),
            "Explain neural networks.",
            "en",
            3,
            7,
            "grounded-v1",
            (evidence,),
        )
