"""Tests for deterministic request-local evidence segmentation."""

from __future__ import annotations

import pytest

from aaos.rag.contracts import (
    RagChunkSourceSpanValue,
    RagErrorCode,
    RagOperationalError,
    RetrievedEvidence,
)
from aaos.rag.evidence_segments import EvidenceSegmentSourceSpan, segment_selected_evidence

GENERATION_ID = 2
CONTENT_REVISION = 4


def _evidence(
    text: str,
    *,
    evidence_id: str = "ev-1",
    source_spans: tuple[RagChunkSourceSpanValue, ...] | None = None,
) -> RetrievedEvidence:
    spans = (
        source_spans
        if source_spans is not None
        else (RagChunkSourceSpanValue(1, 1, 0, len(text), 0, len(text)),)
    )
    return RetrievedEvidence(
        evidence_id=evidence_id,
        chunk_key=f"chunk-{evidence_id}",
        exact_text=text,
        heading=None,
        document_version_id=10,
        document_title="source.pdf",
        course_id=3,
        source_spans=spans,
        dense_rank=1,
        lexical_rank=1,
        fused_rank=1,
        generation_id=GENERATION_ID,
        content_revision=CONTENT_REVISION,
    )


def test_identical_selected_evidence_has_stable_unique_ids_and_exact_reconstruction() -> None:
    evidence = (
        _evidence("First paragraph has enough text.\n\nSecond paragraph has Arabic العربية."),
        _evidence("Another selected source.", evidence_id="ev-2"),
    )

    first = segment_selected_evidence(evidence)
    second = segment_selected_evidence(evidence)

    assert first == second
    assert tuple(segment.segment_id for segment in first) == ("s001", "s002")
    assert len({segment.segment_id for segment in first}) == len(first)
    for parent in evidence:
        children = tuple(item for item in first if item.parent_evidence_id == parent.evidence_id)
        assert "".join(item.exact_text for item in children) == parent.exact_text
        assert children[0].start_offset == 0
        assert children[-1].end_offset == len(parent.exact_text)
        assert all(
            len(item.exact_text) <= 400
            and parent.exact_text[item.start_offset : item.end_offset] == item.exact_text
            for item in children
        )
        assert all(
            left.end_offset == right.start_offset for left, right in zip(children, children[1:])
        )


def test_segmentation_uses_rightmost_natural_boundary_before_limit() -> None:
    from aaos.rag.evidence_segments import segment_selected_evidence

    first_sentence = "A" * 100 + ". "
    second_sentence = "B" * 100 + ". "
    third_sentence = "C" * 100 + ". "
    text = first_sentence + second_sentence + third_sentence + ("tail" * 100)

    segments = segment_selected_evidence((_evidence(text),))

    assert segments[0].exact_text == first_sentence + second_sentence + third_sentence
    assert segments[1].exact_text == "tail" * 100


def test_segmentation_falls_back_to_whitespace_then_hard_split() -> None:
    from aaos.rag.evidence_segments import segment_selected_evidence

    whitespace_text = "word " * 99 + "word"
    whitespace_segments = segment_selected_evidence((_evidence(whitespace_text),))
    assert len(whitespace_segments) == 2
    assert all(len(segment.exact_text) <= 400 for segment in whitespace_segments)
    assert "".join(segment.exact_text for segment in whitespace_segments) == whitespace_text

    hard_text = "x" * 801
    hard_segments = segment_selected_evidence((_evidence(hard_text),))
    assert tuple(len(segment.exact_text) for segment in hard_segments) == (400, 400, 1)
    assert "".join(segment.exact_text for segment in hard_segments) == hard_text


def test_segment_source_intersection_maps_cross_page_offsets() -> None:
    from aaos.rag.evidence_segments import segment_selected_evidence

    text = "A" * 250 + "B" * 250
    evidence = _evidence(
        text,
        source_spans=(
            RagChunkSourceSpanValue(10, 3, 100, 350, 0, 250),
            RagChunkSourceSpanValue(11, 4, 500, 750, 250, 500),
        ),
    )

    segments = segment_selected_evidence((evidence,))

    assert len(segments) == 2
    assert isinstance(segments[0].source_spans[0], EvidenceSegmentSourceSpan)
    assert isinstance(segments[1].source_spans[0], EvidenceSegmentSourceSpan)
    assert segments[0].source_spans[0].document_page_id == 10
    assert segments[0].source_spans[0].source_char_start == 100
    assert segments[0].source_spans[0].source_char_end == 350
    assert segments[1].source_spans[0].document_page_id == 11
    assert segments[1].source_spans[0].segment_char_start == 0
    assert segments[1].source_spans[0].segment_char_end == 100


def test_segment_source_intersection_preserves_exact_source_boundaries() -> None:
    from aaos.rag.evidence_segments import EvidenceSegmentSourceSpan, segment_selected_evidence

    text = "A" * 400 + "B" * 100
    evidence = _evidence(
        text,
        source_spans=(
            RagChunkSourceSpanValue(10, 3, 20, 420, 0, 400),
            RagChunkSourceSpanValue(11, 4, 800, 900, 400, 500),
        ),
    )

    segments = segment_selected_evidence((evidence,))

    assert segments[0].source_spans == (EvidenceSegmentSourceSpan(10, 3, 20, 420, 0, 400),)
    assert segments[1].source_spans == (EvidenceSegmentSourceSpan(11, 4, 800, 900, 0, 100),)


def test_segment_source_intersection_rejects_uncovered_parent_text() -> None:
    from aaos.rag.evidence_segments import segment_selected_evidence

    text = "A" * 500
    evidence = _evidence(
        text,
        source_spans=(
            RagChunkSourceSpanValue(10, 3, 0, 200, 0, 200),
            RagChunkSourceSpanValue(11, 4, 300, 500, 300, 500),
        ),
    )

    with pytest.raises(RagOperationalError) as error:
        segment_selected_evidence((evidence,))

    assert error.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED


def test_segment_resolution_deduplicates_and_discards_unknown_ids() -> None:
    from aaos.rag.evidence_segments import resolve_segment_selection, segment_selected_evidence

    evidence = (_evidence("The first source."),)
    segments = segment_selected_evidence(evidence)

    resolved = resolve_segment_selection(
        (segments[0].segment_id, "s999", segments[0].segment_id),
        segments=segments,
        evidence=evidence,
        pinned_generation_id=GENERATION_ID,
        pinned_content_revision=CONTENT_REVISION,
    )

    assert len(resolved) == 1
    assert resolved[0].evidence_id == "ev-1"
    assert resolved[0].quote == evidence[0].exact_text


def test_segment_resolution_rejects_forged_provenance() -> None:
    from aaos.rag.evidence_segments import (
        EvidenceSegment,
        EvidenceSegmentSourceSpan,
        resolve_segment_selection,
    )

    forged = EvidenceSegment(
        segment_id="s001",
        parent_evidence_id="ev-1",
        exact_text="forged",
        start_offset=0,
        end_offset=6,
        source_spans=(EvidenceSegmentSourceSpan(1, 1, 0, 6, 0, 6),),
    )

    with pytest.raises(RagOperationalError) as error:
        resolve_segment_selection(
            ("s001",),
            segments=(forged,),
            evidence=(_evidence("authoritative"),),
            pinned_generation_id=GENERATION_ID,
            pinned_content_revision=CONTENT_REVISION,
        )

    assert error.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED
