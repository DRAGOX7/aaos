"""Deterministic, request-local segmentation of selected RAG evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import (
    EvidenceSpan,
    RagChunkSourceSpanValue,
    RagChunkStructureSourceSpanValue,
    RagErrorCode,
    RagOperationalError,
    RetrievedEvidence,
)

SEGMENTATION_VERSION = "request-evidence-segments-v1"
SEGMENT_MAX_CHARACTERS = 400
MAX_SELECTED_SEGMENTS_PER_REQUIREMENT = 4

_SEGMENT_ID_PATTERN = re.compile(r"^s[0-9]{3,}$")
_SENTENCE_TERMINALS = frozenset({".", "?", "!", "。", "؟", "؛"})


@dataclass(frozen=True, slots=True)
class EvidenceSegmentSourceSpan:
    """Trusted source interval for one request-local evidence segment."""

    document_page_id: int
    page_number: int
    source_char_start: int
    source_char_end: int
    segment_char_start: int
    segment_char_end: int

    def __post_init__(self) -> None:
        integer_fields = (
            self.document_page_id,
            self.page_number,
            self.source_char_start,
            self.source_char_end,
            self.segment_char_start,
            self.segment_char_end,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
            raise ValueError("segment source spans require integer offsets")
        if self.document_page_id <= 0 or self.page_number <= 0:
            raise ValueError("segment source identity must be positive")
        if self.source_char_start < 0 or self.source_char_end <= self.source_char_start:
            raise ValueError("source character span must be monotonic and non-empty")
        if self.segment_char_start < 0 or self.segment_char_end <= self.segment_char_start:
            raise ValueError("segment character span must be monotonic and non-empty")


@dataclass(frozen=True, slots=True)
class EvidenceSegmentStructureSourceSpan:
    """Trusted structural source interval for one request-local segment."""

    document_structure_unit_id: int
    unit_ordinal: int
    kind: str
    heading_level: int | None
    table_index: int | None
    row_index: int | None
    column_index: int | None
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int
    segment_char_start: int
    segment_char_end: int

    def __post_init__(self) -> None:
        try:
            RagChunkStructureSourceSpanValue(
                document_structure_unit_id=self.document_structure_unit_id,
                unit_ordinal=self.unit_ordinal,
                kind=self.kind,
                heading_level=self.heading_level,
                table_index=self.table_index,
                row_index=self.row_index,
                column_index=self.column_index,
                source_char_start=self.source_char_start,
                source_char_end=self.source_char_end,
                chunk_char_start=self.chunk_char_start,
                chunk_char_end=self.chunk_char_end,
            )
        except (TypeError, ValueError):
            raise ValueError("invalid structural source span") from None

        segment_offsets = (self.segment_char_start, self.segment_char_end)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in segment_offsets):
            raise ValueError("segment character offsets require integers")
        if self.segment_char_start < 0 or self.segment_char_end <= self.segment_char_start:
            raise ValueError("segment character span must be monotonic and non-empty")
        if (
            self.source_char_end - self.source_char_start
            != self.chunk_char_end - self.chunk_char_start
            or self.chunk_char_end - self.chunk_char_start
            != self.segment_char_end - self.segment_char_start
        ):
            raise ValueError("structural source, chunk, and segment spans must align")


EvidenceSegmentSourceSpanValue = EvidenceSegmentSourceSpan | EvidenceSegmentStructureSourceSpan


@dataclass(frozen=True, slots=True)
class EvidenceSegment:
    """One exact, non-overlapping request-local slice of selected evidence."""

    segment_id: str
    parent_evidence_id: str
    exact_text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[EvidenceSegmentSourceSpanValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not _SEGMENT_ID_PATTERN.fullmatch(
            self.segment_id
        ):
            raise ValueError("segment_id must use the sNNN format")
        if not isinstance(self.parent_evidence_id, str) or not self.parent_evidence_id.strip():
            raise ValueError("parent_evidence_id must be non-blank")
        if not isinstance(self.exact_text, str) or not self.exact_text.strip():
            raise ValueError("exact_text must be non-blank")
        if len(self.exact_text) > SEGMENT_MAX_CHARACTERS:
            raise ValueError("exact_text exceeds the segment bound")
        offsets = (self.start_offset, self.end_offset)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in offsets):
            raise ValueError("segment offsets require integers")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("segment offsets must be monotonic and non-empty")
        if self.end_offset - self.start_offset != len(self.exact_text):
            raise ValueError("segment offsets must match exact_text")
        if not isinstance(self.source_spans, tuple) or not self.source_spans:
            raise ValueError("source_spans must be non-empty")
        if all(isinstance(span, EvidenceSegmentSourceSpan) for span in self.source_spans):
            if self.source_spans[0].segment_char_start != 0:
                raise ValueError("segment source mapping must begin at zero")
            if self.source_spans[-1].segment_char_end != len(self.exact_text):
                raise ValueError("segment source mapping must cover exact_text")
            for previous, current in zip(self.source_spans, self.source_spans[1:]):
                if previous.segment_char_end != current.segment_char_start:
                    raise ValueError("segment source mappings must be contiguous")
            return

        if not all(
            isinstance(span, EvidenceSegmentStructureSourceSpan) for span in self.source_spans
        ):
            raise ValueError("segment source mappings must use one supported family")

        previous_end = 0
        for span in self.source_spans:
            if not isinstance(span, EvidenceSegmentStructureSourceSpan):
                raise ValueError("invalid structural segment source span")
            if span.segment_char_end > len(self.exact_text):
                raise ValueError("structural segment source span exceeds exact_text")
            if (
                span.chunk_char_start != self.start_offset + span.segment_char_start
                or span.chunk_char_end != self.start_offset + span.segment_char_end
            ):
                raise ValueError("structural chunk offsets must match segment offsets")
            if span.segment_char_start < previous_end:
                raise ValueError("structural segment source mappings must not overlap")
            previous_end = span.segment_char_end


def segment_selected_evidence(
    evidence: tuple[RetrievedEvidence, ...],
) -> tuple[EvidenceSegment, ...]:
    """Partition selected evidence into deterministic exact-text segments."""
    _validate_evidence_tuple(evidence)
    if not evidence:
        return ()
    evidence_ids = tuple(item.evidence_id for item in evidence)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    generation_id = evidence[0].generation_id
    content_revision = evidence[0].content_revision
    if any(
        item.generation_id != generation_id or item.content_revision != content_revision
        for item in evidence
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

    segments: list[EvidenceSegment] = []
    next_id = 1
    for item in evidence:
        start = 0
        while start < len(item.exact_text):
            end = _choose_segment_end(item.exact_text, start)
            if end <= start:
                raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
            exact_text = item.exact_text[start:end]
            try:
                source_spans = _map_source_spans(item, start, end)
                segment = EvidenceSegment(
                    segment_id=f"s{next_id:03d}",
                    parent_evidence_id=item.evidence_id,
                    exact_text=exact_text,
                    start_offset=start,
                    end_offset=end,
                    source_spans=source_spans,
                )
            except (TypeError, ValueError):
                raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED) from None
            segments.append(segment)
            next_id += 1
            start = end
    return tuple(segments)


def resolve_segment_selection(
    selected_ids: tuple[str, ...],
    *,
    segments: tuple[EvidenceSegment, ...],
    evidence: tuple[RetrievedEvidence, ...],
    pinned_generation_id: int,
    pinned_content_revision: int,
) -> tuple[EvidenceSpan, ...]:
    """Resolve untrusted IDs to trusted existing EvidenceSpan values."""
    if (
        not isinstance(selected_ids, tuple)
        or len(selected_ids) > MAX_SELECTED_SEGMENTS_PER_REQUIREMENT
    ):
        raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
    _validate_evidence_tuple(evidence)
    if any(
        item.generation_id != pinned_generation_id
        or item.content_revision != pinned_content_revision
        for item in evidence
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if not isinstance(segments, tuple) or not all(
        isinstance(segment, EvidenceSegment) for segment in segments
    ):
        raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
    segment_by_id: dict[str, EvidenceSegment] = {}
    for segment in segments:
        if segment.segment_id in segment_by_id:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        segment_by_id[segment.segment_id] = segment
    evidence_by_id = {item.evidence_id: item for item in evidence}
    for segment in segments:
        parent = evidence_by_id.get(segment.parent_evidence_id)
        if parent is None:
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
        _validate_segment_against_parent(segment, parent)

    resolved: list[EvidenceSpan] = []
    seen: set[str] = set()
    for segment_id in selected_ids:
        if not isinstance(segment_id, str):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        selected_segment = segment_by_id.get(segment_id)
        if selected_segment is None or segment_id in seen:
            continue
        seen.add(segment_id)
        try:
            resolved.append(
                EvidenceSpan(selected_segment.parent_evidence_id, selected_segment.exact_text)
            )
        except (TypeError, ValueError):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED) from None
    return tuple(resolved)


def _validate_evidence_tuple(evidence: tuple[RetrievedEvidence, ...]) -> None:
    if not isinstance(evidence, tuple) or not all(
        isinstance(item, RetrievedEvidence) for item in evidence
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)


def _choose_segment_end(text: str, start: int) -> int:
    limit = min(start + SEGMENT_MAX_CHARACTERS, len(text))
    if limit == len(text):
        return limit
    for boundary_kind in ("paragraph", "line", "sentence", "whitespace"):
        candidates = _boundary_candidates(text, start, limit, boundary_kind)
        if candidates:
            return max(candidates)
    return limit


def _boundary_candidates(text: str, start: int, limit: int, kind: str) -> list[int]:
    candidates: list[int] = []
    if kind == "paragraph":
        for match in re.finditer(r"(?:\r\n|\n)[ \t]*(?:\r\n|\n)", text[start:limit]):
            end = start + match.end()
            if end > start:
                candidates.append(end)
        return candidates
    if kind == "line":
        for match in re.finditer(r"\r\n|\n", text[start:limit]):
            end = start + match.end()
            if end > start:
                candidates.append(end)
        return candidates
    if kind == "sentence":
        for index in range(start, limit):
            if text[index] not in _SENTENCE_TERMINALS:
                continue
            next_index = index + 1
            if next_index < len(text) and not text[next_index].isspace():
                continue
            end = next_index
            while end < limit and text[end].isspace():
                end += 1
            if end > start:
                candidates.append(end)
        return candidates
    if kind == "whitespace":
        for index in range(start, limit):
            if text[index].isspace():
                candidates.append(index + 1)
        return candidates
    raise ValueError("unknown segment boundary kind")


def _map_source_spans(
    evidence: RetrievedEvidence,
    start: int,
    end: int,
) -> tuple[EvidenceSegmentSourceSpanValue, ...]:
    intersections: list[EvidenceSegmentSourceSpanValue] = []
    for span in evidence.source_spans:
        if evidence.provenance_mode == "PAGE":
            if not isinstance(span, RagChunkSourceSpanValue):
                raise ValueError("PAGE evidence requires page source spans")
        elif evidence.provenance_mode == "STRUCTURE":
            if not isinstance(span, RagChunkStructureSourceSpanValue):
                raise ValueError("STRUCTURE evidence requires structural source spans")
        else:
            raise ValueError("unknown evidence provenance mode")
        if (
            span.chunk_char_start < 0
            or span.chunk_char_end <= span.chunk_char_start
            or span.chunk_char_end > len(evidence.exact_text)
        ):
            raise ValueError("source span falls outside parent text")
        overlap_start = max(start, span.chunk_char_start)
        overlap_end = min(end, span.chunk_char_end)
        if overlap_start >= overlap_end:
            continue
        source_start = span.source_char_start + (overlap_start - span.chunk_char_start)
        source_end = source_start + (overlap_end - overlap_start)
        if isinstance(span, RagChunkSourceSpanValue):
            intersections.append(
                EvidenceSegmentSourceSpan(
                    document_page_id=span.document_page_id,
                    page_number=span.page_number,
                    source_char_start=source_start,
                    source_char_end=source_end,
                    segment_char_start=overlap_start - start,
                    segment_char_end=overlap_end - start,
                )
            )
        elif isinstance(span, RagChunkStructureSourceSpanValue):
            intersections.append(
                EvidenceSegmentStructureSourceSpan(
                    document_structure_unit_id=span.document_structure_unit_id,
                    unit_ordinal=span.unit_ordinal,
                    kind=span.kind,
                    heading_level=span.heading_level,
                    table_index=span.table_index,
                    row_index=span.row_index,
                    column_index=span.column_index,
                    source_char_start=source_start,
                    source_char_end=source_end,
                    chunk_char_start=overlap_start,
                    chunk_char_end=overlap_end,
                    segment_char_start=overlap_start - start,
                    segment_char_end=overlap_end - start,
                )
            )
        else:
            raise ValueError("invalid source span")

    if not intersections:
        raise ValueError("source spans do not cover segment")
    if evidence.provenance_mode == "PAGE":
        if intersections[0].segment_char_start != 0:
            raise ValueError("source spans do not cover segment start")
        if intersections[-1].segment_char_end != end - start:
            raise ValueError("source spans do not cover segment end")
    for previous, current in zip(intersections, intersections[1:]):
        if evidence.provenance_mode == "PAGE":
            if previous.segment_char_end != current.segment_char_start:
                raise ValueError("source spans are not contiguous")
        elif previous.segment_char_end > current.segment_char_start:
            raise ValueError("structural source spans overlap")
    return tuple(intersections)


def _validate_segment_against_parent(
    segment: EvidenceSegment,
    parent: RetrievedEvidence,
) -> None:
    if (
        segment.start_offset < 0
        or segment.end_offset > len(parent.exact_text)
        or parent.exact_text[segment.start_offset : segment.end_offset] != segment.exact_text
        or segment.start_offset >= segment.end_offset
    ):
        raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
    expected = _map_source_spans(parent, segment.start_offset, segment.end_offset)
    if expected != segment.source_spans:
        raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
