from __future__ import annotations

from types import SimpleNamespace

from pytest import MonkeyPatch

from aaos.rag.contracts import (
    GlobalScope,
    RagChunkStructureSourceSpanValue,
    RagGenerationIdentity,
)
from aaos.rag.repository import (
    AuthoritativeRetrievalChunk,
    QuerySnapshot,
    RagRepository,
    StructureSourceSpanRecord,
)
from aaos.rag.retrieval import (
    EvidenceSelector,
    FusedCandidate,
    _span_interval,
)


def _structure_span(
    *,
    source_span_id: int = 1,
    rag_chunk_id: int = 1,
    document_structure_unit_id: int = 11,
    ordinal: int = 1,
    source_char_start: int = 0,
    source_char_end: int = 15,
    chunk_char_start: int = 0,
    chunk_char_end: int = 15,
) -> StructureSourceSpanRecord:
    return StructureSourceSpanRecord(
        source_span_id=source_span_id,
        rag_chunk_id=rag_chunk_id,
        document_structure_unit_id=(
            document_structure_unit_id
        ),
        ordinal=ordinal,
        unit_ordinal=1,
        kind="HEADING",
        heading_level=1,
        table_index=None,
        row_index=None,
        column_index=None,
        source_char_start=source_char_start,
        source_char_end=source_char_end,
        chunk_char_start=chunk_char_start,
        chunk_char_end=chunk_char_end,
    )


def test_structural_span_interval_uses_unit_identity() -> None:
    span = _structure_span(
        document_structure_unit_id=17,
        source_char_start=3,
        source_char_end=12,
    )

    assert _span_interval(span) == (
        17,
        3,
        12,
    )


def test_evidence_selector_projects_structural_provenance(
    monkeypatch: MonkeyPatch,
) -> None:
    exact_text = "Neural Networks"

    chunk = SimpleNamespace(
        chunk_id=1,
        generation_id=1,
        document_version_id=10,
        chunk_key="a" * 64,
        ordinal=1,
        heading="Neural Networks",
        exact_text=exact_text,
        token_count=2,
        provenance_mode="STRUCTURE",
    )

    version = SimpleNamespace(
        document_version_id=10,
        document_id=3,
        course_id=7,
        source_filename="notes.docx",
        provenance_mode="STRUCTURE",
    )

    span = _structure_span(
        rag_chunk_id=1,
        document_structure_unit_id=17,
        source_char_start=0,
        source_char_end=len(exact_text),
        chunk_char_start=0,
        chunk_char_end=len(exact_text),
    )

    authoritative = AuthoritativeRetrievalChunk(
        chunk=chunk,  # type: ignore[arg-type]
        document_version=version,  # type: ignore[arg-type]
        source_spans=(span,),
    )

    snapshot = QuerySnapshot(
        RagGenerationIdentity(
            1,
            "f" * 64,
            0,
            3,
        ),
        GlobalScope(),
        (10,),
        "neural networks",
    )

    candidate = FusedCandidate(
        chunk_key="a" * 64,
        dense_rank=1,
        lexical_rank=None,
        lexical_document_version_id=None,
        fused_score=1.0 / 61.0,
        fused_rank=1,
    )

    repository = object.__new__(RagRepository)

    def materialize(
        self: RagRepository,
        received_snapshot: QuerySnapshot,
        chunk_keys: tuple[str, ...],
    ) -> tuple[AuthoritativeRetrievalChunk, ...]:
        del self
        assert received_snapshot == snapshot
        assert chunk_keys == ("a" * 64,)
        return (authoritative,)

    monkeypatch.setattr(
        RagRepository,
        "materialize_snapshot_chunks",
        materialize,
    )

    selector = EvidenceSelector(
        repository=repository,
        evidence_id_factory=lambda: "evidence-1",
    )

    evidence = selector.select(
        snapshot,
        (candidate,),
    )

    assert len(evidence) == 1

    item = evidence[0]

    assert item.provenance_mode == "STRUCTURE"
    assert item.exact_text == exact_text
    assert len(item.source_spans) == 1

    projected = item.source_spans[0]

    assert isinstance(
        projected,
        RagChunkStructureSourceSpanValue,
    )
    assert projected.document_structure_unit_id == 17
    assert projected.unit_ordinal == 1
    assert projected.kind == "HEADING"
    assert projected.heading_level == 1

    assert not hasattr(projected, "page_number")
    assert not hasattr(projected, "document_page_id")
