"""Structural provenance coverage for semantic evidence segmentation."""

from __future__ import annotations

import asyncio

import pytest

from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.rag.contracts import (
    EvidenceSpan,
    RagChunkSourceSpanValue,
    RagChunkStructureSourceSpanValue,
    RagErrorCode,
    RagOperationalError,
    Requirement,
    RequirementEvidenceSpans,
    RequirementSet,
    RetrievedEvidence,
)
from aaos.rag.evidence_segments import (
    EvidenceSegmentSourceSpan,
    EvidenceSegmentStructureSourceSpan,
    segment_selected_evidence,
)
from aaos.rag.semantic_coverage import EvidenceSpanExtractor

GENERATION_ID = 2
CONTENT_REVISION = 4


def _page_evidence(text: str, *, evidence_id: str = "ev-page") -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=evidence_id,
        chunk_key=f"chunk-{evidence_id}",
        exact_text=text,
        heading=None,
        document_version_id=10,
        document_title="source.pdf",
        course_id=3,
        source_spans=(RagChunkSourceSpanValue(1, 1, 0, len(text), 0, len(text)),),
        dense_rank=1,
        lexical_rank=1,
        fused_rank=1,
        generation_id=GENERATION_ID,
        content_revision=CONTENT_REVISION,
    )


def _structure_span(
    *,
    unit_id: int,
    ordinal: int,
    kind: str,
    heading_level: int | None,
    source_start: int,
    source_end: int,
    chunk_start: int,
    chunk_end: int,
) -> RagChunkStructureSourceSpanValue:
    return RagChunkStructureSourceSpanValue(
        document_structure_unit_id=unit_id,
        unit_ordinal=ordinal,
        kind=kind,
        heading_level=heading_level,
        table_index=None,
        row_index=None,
        column_index=None,
        source_char_start=source_start,
        source_char_end=source_end,
        chunk_char_start=chunk_start,
        chunk_char_end=chunk_end,
    )


def _structure_evidence(
    text: str,
    *,
    evidence_id: str = "ev-structure",
    source_spans: tuple[RagChunkStructureSourceSpanValue, ...] | None = None,
) -> RetrievedEvidence:
    spans = source_spans or (
        _structure_span(
            unit_id=17,
            ordinal=4,
            kind="PARAGRAPH",
            heading_level=None,
            source_start=23,
            source_end=23 + len(text),
            chunk_start=0,
            chunk_end=len(text),
        ),
    )
    return RetrievedEvidence(
        evidence_id=evidence_id,
        chunk_key=f"chunk-{evidence_id}",
        exact_text=text,
        heading="Neural Networks",
        document_version_id=10,
        document_title="notes.docx",
        course_id=3,
        source_spans=spans,
        dense_rank=1,
        lexical_rank=None,
        fused_rank=1,
        generation_id=GENERATION_ID,
        content_revision=CONTENT_REVISION,
        provenance_mode="STRUCTURE",
    )


class FakeSpanGateway:
    def __init__(self, segment_ids: tuple[str, ...]) -> None:
        self.requests: list[ChatRequest] = []
        ids = ",".join(f'"{segment_id}"' for segment_id in segment_ids)
        self.response = ChatResponse(
            content=f'{{"segment_ids":[{ids}]}}',
            model_identifier="qwen/qwen3.5-4b",
        )

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(
            True,
            True,
            "qwen/qwen3.5-4b",
            ("qwen/qwen3.5-4b",),
            (8192,),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return self.response


def _requirements() -> RequirementSet:
    return RequirementSet((Requirement("r1", "Explain the source."),))


def test_structural_evidence_is_extracted_through_the_real_segmenter() -> None:
    text = "Neural networks use connected units to learn patterns."
    evidence = _structure_evidence(text)
    gateway = FakeSpanGateway(("s001",))

    result = asyncio.run(
        EvidenceSpanExtractor(gateway).extract(_requirements(), evidence=(evidence,))
    )

    assert result == (RequirementEvidenceSpans("r1", (EvidenceSpan(evidence.evidence_id, text),)),)
    assert len(gateway.requests) == 1
    assert text in gateway.requests[0].messages[-1].content


def test_structural_segment_offsets_map_exactly_to_source_and_chunk_text() -> None:
    heading = "Heading"
    body = " body content remains mapped exactly. " * 18
    text = heading + body
    source_heading_start = 23
    source_body_start = 211
    source_text = (
        "x" * source_heading_start
        + heading
        + "y" * (source_body_start - source_heading_start - len(heading))
        + body
    )
    spans = (
        _structure_span(
            unit_id=17,
            ordinal=4,
            kind="HEADING",
            heading_level=2,
            source_start=source_heading_start,
            source_end=source_heading_start + len(heading),
            chunk_start=0,
            chunk_end=len(heading),
        ),
        _structure_span(
            unit_id=18,
            ordinal=5,
            kind="PARAGRAPH",
            heading_level=None,
            source_start=source_body_start,
            source_end=source_body_start + len(body),
            chunk_start=len(heading),
            chunk_end=len(text),
        ),
    )
    evidence = _structure_evidence(text, source_spans=spans)

    segments = segment_selected_evidence((evidence,))

    assert "".join(segment.exact_text for segment in segments) == text
    assert len(segments) > 1
    for segment in segments:
        assert evidence.exact_text[segment.start_offset : segment.end_offset] == segment.exact_text
        for source_span in segment.source_spans:
            assert isinstance(source_span, EvidenceSegmentStructureSourceSpan)
            chunk_start = segment.start_offset + source_span.segment_char_start
            chunk_end = segment.start_offset + source_span.segment_char_end
            assert source_span.chunk_char_start == chunk_start
            assert source_span.chunk_char_end == chunk_end
            assert (
                source_text[source_span.source_char_start : source_span.source_char_end]
                == (evidence.exact_text[chunk_start:chunk_end])
            )
            assert not hasattr(source_span, "document_page_id")
            assert not hasattr(source_span, "page_number")
            assert source_span.document_structure_unit_id in {17, 18}


def test_chunk_separators_remain_unattributed_in_structural_segments() -> None:
    heading = "Heading"
    separator = "\n\n"
    paragraph = "Paragraph text."
    text = heading + separator + paragraph
    paragraph_start = len(heading) + len(separator)
    evidence = _structure_evidence(
        text,
        source_spans=(
            _structure_span(
                unit_id=17,
                ordinal=4,
                kind="HEADING",
                heading_level=2,
                source_start=23,
                source_end=23 + len(heading),
                chunk_start=0,
                chunk_end=len(heading),
            ),
            _structure_span(
                unit_id=18,
                ordinal=5,
                kind="PARAGRAPH",
                heading_level=None,
                source_start=71,
                source_end=71 + len(paragraph),
                chunk_start=paragraph_start,
                chunk_end=len(text),
            ),
        ),
    )

    segments = segment_selected_evidence((evidence,))

    assert len(segments) == 1
    assert segments[0].exact_text == text
    structural_spans = tuple(
        span
        for span in segments[0].source_spans
        if isinstance(span, EvidenceSegmentStructureSourceSpan)
    )
    assert len(structural_spans) == 2
    assert tuple((span.chunk_char_start, span.chunk_char_end) for span in structural_spans) == (
        (0, len(heading)),
        (paragraph_start, len(text)),
    )


def test_page_segment_mapping_keeps_page_identity_and_exact_offsets() -> None:
    text = "A" * 400 + "B" * 100
    evidence = RetrievedEvidence(
        evidence_id="ev-page",
        chunk_key="chunk-page",
        exact_text=text,
        heading=None,
        document_version_id=10,
        document_title="source.pdf",
        course_id=3,
        source_spans=(
            RagChunkSourceSpanValue(10, 3, 20, 420, 0, 400),
            RagChunkSourceSpanValue(11, 4, 800, 900, 400, 500),
        ),
        dense_rank=1,
        lexical_rank=1,
        fused_rank=1,
        generation_id=GENERATION_ID,
        content_revision=CONTENT_REVISION,
    )

    segments = segment_selected_evidence((evidence,))

    assert tuple(segment.exact_text for segment in segments) == ("A" * 400, "B" * 100)
    page_spans = tuple(
        span
        for segment in segments
        for span in segment.source_spans
        if isinstance(span, EvidenceSegmentSourceSpan)
    )
    assert len(page_spans) == 2
    assert tuple(
        (
            span.document_page_id,
            span.page_number,
            span.source_char_start,
            span.source_char_end,
            span.segment_char_start,
            span.segment_char_end,
        )
        for span in page_spans
    ) == ((10, 3, 20, 420, 0, 400), (11, 4, 800, 900, 0, 100))


def test_mixed_page_and_structure_evidence_is_extracted_without_fake_pages() -> None:
    page = _page_evidence("PDF source text.")
    structure = _structure_evidence("DOCX structural text.")
    gateway = FakeSpanGateway(("s001", "s002"))

    result = asyncio.run(
        EvidenceSpanExtractor(gateway).extract(_requirements(), evidence=(page, structure))
    )

    assert result == (
        RequirementEvidenceSpans(
            "r1",
            (
                EvidenceSpan(page.evidence_id, page.exact_text),
                EvidenceSpan(structure.evidence_id, structure.exact_text),
            ),
        ),
    )
    assert len(gateway.requests) == 1
    assert not hasattr(structure.source_spans[0], "document_page_id")
    assert not hasattr(structure.source_spans[0], "page_number")


@pytest.mark.parametrize(
    ("evidence_family", "span_family"),
    (("PAGE", "STRUCTURE"), ("STRUCTURE", "PAGE")),
)
def test_segmenter_rejects_wrong_family_provenance(
    evidence_family: str,
    span_family: str,
) -> None:
    evidence = (
        _page_evidence("Wrong family.")
        if span_family == "PAGE"
        else _structure_evidence("Wrong family.")
    )
    object.__setattr__(evidence, "provenance_mode", evidence_family)

    with pytest.raises(RagOperationalError) as error:
        segment_selected_evidence((evidence,))

    assert error.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED
