from __future__ import annotations

from types import SimpleNamespace

from pytest import MonkeyPatch

from aaos.rag.contracts import (
    Citation,
    GlobalScope,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagChunkStructureSourceSpanValue,
    RagClaim,
    RagGenerationIdentity,
    StructuralCitation,
)
from aaos.rag.generation import (
    CitationValidator,
    GroundedModelOutput,
    render_rag_answer,
)
from aaos.rag.repository import (
    AuthoritativeRetrievalChunk,
    QuerySnapshot,
    RagRepository,
    SourceSpanRecord,
    StructureSourceSpanRecord,
)
from aaos.rag.retrieval import (
    EvidenceSelector,
    FusedCandidate,
)


def test_mixed_page_and_structure_survive_retrieval_and_citation(
    monkeypatch: MonkeyPatch,
) -> None:
    generation = RagGenerationIdentity(
        1,
        "f" * 64,
        0,
        3,
    )

    snapshot = QuerySnapshot(
        generation,
        GlobalScope(),
        (10, 20),
        "mixed provenance",
    )

    page_text = "PDF evidence"
    structure_text = "Neural Networks"

    page_chunk = SimpleNamespace(
        chunk_id=1,
        generation_id=1,
        document_version_id=10,
        chunk_key="a" * 64,
        ordinal=1,
        heading=None,
        exact_text=page_text,
        token_count=2,
        provenance_mode="PAGE",
    )
    page_version = SimpleNamespace(
        document_version_id=10,
        document_id=1,
        course_id=7,
        source_filename="lecture.pdf",
        provenance_mode="PAGE",
    )
    page_span = SourceSpanRecord(
        source_span_id=1,
        rag_chunk_id=1,
        document_page_id=101,
        ordinal=1,
        page_number=2,
        page_char_start=0,
        page_char_end=len(page_text),
        chunk_char_start=0,
        chunk_char_end=len(page_text),
    )

    structure_chunk = SimpleNamespace(
        chunk_id=2,
        generation_id=1,
        document_version_id=20,
        chunk_key="b" * 64,
        ordinal=1,
        heading="Neural Networks",
        exact_text=structure_text,
        token_count=2,
        provenance_mode="STRUCTURE",
    )
    structure_version = SimpleNamespace(
        document_version_id=20,
        document_id=2,
        course_id=7,
        source_filename="notes.docx",
        provenance_mode="STRUCTURE",
    )
    structure_span = StructureSourceSpanRecord(
        source_span_id=2,
        rag_chunk_id=2,
        document_structure_unit_id=201,
        ordinal=1,
        unit_ordinal=4,
        kind="HEADING",
        heading_level=2,
        table_index=None,
        row_index=None,
        column_index=None,
        source_char_start=0,
        source_char_end=len(structure_text),
        chunk_char_start=0,
        chunk_char_end=len(structure_text),
    )

    authoritative = (
        AuthoritativeRetrievalChunk(
            chunk=page_chunk,  # type: ignore[arg-type]
            document_version=page_version,  # type: ignore[arg-type]
            source_spans=(page_span,),
        ),
        AuthoritativeRetrievalChunk(
            chunk=structure_chunk,  # type: ignore[arg-type]
            document_version=structure_version,  # type: ignore[arg-type]
            source_spans=(structure_span,),
        ),
    )

    candidates = (
        FusedCandidate(
            chunk_key="a" * 64,
            dense_rank=1,
            lexical_rank=None,
            lexical_document_version_id=None,
            fused_score=1.0 / 61.0,
            fused_rank=1,
        ),
        FusedCandidate(
            chunk_key="b" * 64,
            dense_rank=2,
            lexical_rank=None,
            lexical_document_version_id=None,
            fused_score=1.0 / 62.0,
            fused_rank=2,
        ),
    )

    repository = object.__new__(RagRepository)

    def materialize(
        self: RagRepository,
        received_snapshot: QuerySnapshot,
        chunk_keys: tuple[str, ...],
    ) -> tuple[AuthoritativeRetrievalChunk, ...]:
        del self
        assert received_snapshot == snapshot
        assert chunk_keys == (
            "a" * 64,
            "b" * 64,
        )
        return authoritative

    monkeypatch.setattr(
        RagRepository,
        "materialize_snapshot_chunks",
        materialize,
    )

    evidence_ids = iter(
        (
            "ev-page",
            "ev-structure",
        )
    )

    selector = EvidenceSelector(
        repository=repository,
        evidence_id_factory=lambda: next(evidence_ids),
    )

    evidence = selector.select(
        snapshot,
        candidates,
    )

    assert len(evidence) == 2

    page_evidence, structure_evidence = evidence

    assert page_evidence.provenance_mode == "PAGE"
    assert structure_evidence.provenance_mode == "STRUCTURE"

    assert isinstance(
        page_evidence.source_spans[0],
        RagChunkSourceSpanValue,
    )
    assert isinstance(
        structure_evidence.source_spans[0],
        RagChunkStructureSourceSpanValue,
    )

    output = GroundedModelOutput(
        status=RagAnswerStatus.ANSWER,
        answer_text="Untrusted aggregate text.",
        language="en",
        claims=(
            RagClaim(
                "claim-page",
                "Page claim.",
                ("ev-page",),
            ),
            RagClaim(
                "claim-structure",
                "Structural claim.",
                ("ev-structure",),
            ),
        ),
        generation_id=1,
        content_revision=0,
        policy_version="grounded-v1",
    )

    answer = CitationValidator().validate(
        output,
        evidence,
    )

    assert answer.answer_text == (
        "Page claim.\n"
        "Structural claim."
    )

    assert len(answer.citations) == 2

    page_citation, structural_citation = (
        answer.citations
    )

    assert isinstance(
        page_citation,
        Citation,
    )
    assert page_citation.page_start == 2
    assert page_citation.page_end == 2

    assert isinstance(
        structural_citation,
        StructuralCitation,
    )
    assert structural_citation.unit_start == 4
    assert structural_citation.unit_end == 4
    assert structural_citation.heading == "Neural Networks"

    assert not hasattr(
        structural_citation,
        "page_start",
    )
    assert not hasattr(
        structural_citation,
        "page_end",
    )

    rendered = render_rag_answer(answer)

    assert rendered.rendered_text == (
        "Page claim.\n"
        "Structural claim.\n\n"
        "[claim-page] lecture.pdf (page 2)\n"
        "[claim-structure] notes.docx "
        "(unit 4; heading Neural Networks)"
    )
