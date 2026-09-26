from __future__ import annotations

from dataclasses import fields
from typing import TypedDict, cast

import pytest

import aaos.rag.contracts as contracts

TEXT = "Neural Networks"


class _RetrievedEvidenceKwargs(TypedDict):
    evidence_id: str
    chunk_key: str
    exact_text: str
    heading: str | None
    document_version_id: int
    document_title: str
    course_id: int
    source_spans: tuple[
        contracts.RagChunkSourceSpanValue | contracts.RagChunkStructureSourceSpanValue,
        ...,
    ]
    dense_rank: int | None
    lexical_rank: int | None
    fused_rank: int
    generation_id: int
    content_revision: int
    token_count: int


def _structure_span_type() -> type[contracts.RagChunkStructureSourceSpanValue]:
    span_type = getattr(
        contracts,
        "RagChunkStructureSourceSpanValue",
        None,
    )
    assert isinstance(span_type, type), "RagChunkStructureSourceSpanValue is missing"
    return cast(type[contracts.RagChunkStructureSourceSpanValue], span_type)


def _structural_span() -> contracts.RagChunkStructureSourceSpanValue:
    span_type = _structure_span_type()
    return span_type(
        document_structure_unit_id=7,
        unit_ordinal=1,
        kind="HEADING",
        heading_level=1,
        table_index=None,
        row_index=None,
        column_index=None,
        source_char_start=0,
        source_char_end=len(TEXT),
        chunk_char_start=0,
        chunk_char_end=len(TEXT),
    )


def _evidence_kwargs(
    source_spans: tuple[
        contracts.RagChunkSourceSpanValue | contracts.RagChunkStructureSourceSpanValue,
        ...,
    ],
) -> _RetrievedEvidenceKwargs:
    return {
        "evidence_id": "ev-1",
        "chunk_key": "chunk-1",
        "exact_text": TEXT,
        "heading": "Neural Networks",
        "document_version_id": 10,
        "document_title": "notes.docx",
        "course_id": 3,
        "source_spans": source_spans,
        "dense_rank": 1,
        "lexical_rank": None,
        "fused_rank": 1,
        "generation_id": 1,
        "content_revision": 0,
        "token_count": 2,
    }


def test_structural_source_span_contract_is_page_free() -> None:
    span_type = _structure_span_type()

    names = {field.name for field in fields(span_type)}

    assert names == {
        "document_structure_unit_id",
        "unit_ordinal",
        "kind",
        "heading_level",
        "table_index",
        "row_index",
        "column_index",
        "source_char_start",
        "source_char_end",
        "chunk_char_start",
        "chunk_char_end",
    }

    assert "page_number" not in names
    assert "document_page_id" not in names


def test_retrieved_evidence_accepts_structural_provenance() -> None:
    span = _structural_span()

    evidence = contracts.RetrievedEvidence(
        **_evidence_kwargs((span,)),
        provenance_mode="STRUCTURE",
    )

    assert evidence.provenance_mode == "STRUCTURE"
    assert evidence.source_spans == (span,)


def test_retrieved_evidence_preserves_legacy_page_default() -> None:
    page_span = contracts.RagChunkSourceSpanValue(
        document_page_id=5,
        page_number=2,
        source_char_start=0,
        source_char_end=len(TEXT),
        chunk_char_start=0,
        chunk_char_end=len(TEXT),
    )

    evidence = contracts.RetrievedEvidence(
        **_evidence_kwargs((page_span,)),
    )

    assert evidence.provenance_mode == "PAGE"


def test_retrieved_evidence_rejects_mixed_provenance() -> None:
    page_span = contracts.RagChunkSourceSpanValue(
        document_page_id=5,
        page_number=2,
        source_char_start=0,
        source_char_end=len(TEXT),
        chunk_char_start=0,
        chunk_char_end=len(TEXT),
    )
    structure_span = _structural_span()

    with pytest.raises(ValueError):
        contracts.RetrievedEvidence(
            **_evidence_kwargs(
                (
                    page_span,
                    structure_span,
                )
            ),
            provenance_mode="STRUCTURE",
        )
