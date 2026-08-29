"""Pure deterministic structure-aware chunking for Phase 4 documents.

This module deliberately has no persistence, filesystem, network, tokenizer
model, or browser dependency.  It turns immutable extracted pages into draft
values that a later indexing task may persist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, Sequence

from .config import RagIndexConfig, compute_config_fingerprint

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)
_PARAGRAPH_PATTERN = re.compile(r"\S.*?(?=\r?\n[ \t]*\r?\n|\Z)", flags=re.DOTALL)
_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(\S.*?)\s*#*[ \t]*$")
_CHUNK_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ChunkCoverage(StrEnum):
    """Deterministic coverage classification for an ordered page set."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Immutable page text and stable Phase 3 provenance identity."""

    document_page_id: int
    document_version_id: int
    page_number: int
    text: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.document_page_id, bool)
            or not isinstance(self.document_page_id, int)
            or self.document_page_id <= 0
        ):
            raise ValueError("document_page_id must be positive")
        if (
            isinstance(self.document_version_id, bool)
            or not isinstance(self.document_version_id, int)
            or self.document_version_id <= 0
        ):
            raise ValueError("document_version_id must be positive")
        if (
            isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or self.page_number <= 0
        ):
            raise ValueError("page_number must be positive")
        if not isinstance(self.text, str):
            raise ValueError("page text must be a string")


class DocumentPageLike(Protocol):
    """Structural input contract also implemented by Phase 3 page records."""

    @property
    def document_page_id(self) -> int: ...

    @property
    def document_version_id(self) -> int: ...

    @property
    def page_number(self) -> int: ...

    @property
    def text(self) -> str: ...


def _coerce_page(value: DocumentPageLike) -> DocumentPage:
    if isinstance(value, DocumentPage):
        return value
    try:
        return DocumentPage(
            document_page_id=value.document_page_id,
            document_version_id=value.document_version_id,
            page_number=value.page_number,
            text=value.text,
        )
    except AttributeError as error:
        raise ValueError("pages must contain DocumentPage values") from error


@dataclass(frozen=True, slots=True)
class ChunkSourceSpanDraft:
    """Exact half-open mapping from a page interval into a chunk interval."""

    document_page_id: int
    page_number: int
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.document_page_id, bool)
            or not isinstance(self.document_page_id, int)
            or self.document_page_id <= 0
        ):
            raise ValueError("document_page_id must be positive")
        if (
            isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or self.page_number <= 0
        ):
            raise ValueError("page_number must be positive")
        for name in (
            "source_char_start",
            "source_char_end",
            "chunk_char_start",
            "chunk_char_end",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.source_char_end <= self.source_char_start:
            raise ValueError("source span must be non-empty")
        if self.chunk_char_end <= self.chunk_char_start:
            raise ValueError("chunk span must be non-empty")
        if (
            self.source_char_end - self.source_char_start
            != self.chunk_char_end - self.chunk_char_start
        ):
            raise ValueError("source and chunk spans must have equal lengths")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Deterministic chunk text plus exact source-span provenance."""

    document_version_id: int
    ordinal: int
    text: str
    token_count: int
    source_spans: tuple[ChunkSourceSpanDraft, ...]
    chunk_key: str
    heading: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.document_version_id, bool)
            or not isinstance(self.document_version_id, int)
            or self.document_version_id <= 0
        ):
            raise ValueError("document_version_id must be positive")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal <= 0:
            raise ValueError("ordinal must be positive")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("chunk text must be non-empty")
        if isinstance(self.token_count, bool) or self.token_count <= 0:
            raise ValueError("token_count must be positive")
        if not self.source_spans:
            raise ValueError("chunk must have source spans")
        if not isinstance(self.source_spans, tuple):
            raise ValueError("source_spans must be an immutable tuple")
        if not isinstance(self.chunk_key, str) or not _CHUNK_KEY_PATTERN.fullmatch(self.chunk_key):
            raise ValueError("chunk_key must be a lowercase SHA-256 hex digest")
        if self.heading is not None and (
            not isinstance(self.heading, str) or not self.heading.strip()
        ):
            raise ValueError("heading must be non-blank text when present")

    @property
    def exact_text(self) -> str:
        """Return the exact text under the persistence contract's name."""
        return self.text

    @property
    def spans(self) -> tuple[ChunkSourceSpanDraft, ...]:
        """Return source spans under the concise draft-facing name."""
        return self.source_spans

    @property
    def text_sha256(self) -> str:
        """Return the deterministic digest of the exact UTF-8 chunk text."""
        return sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def page_start(self) -> int:
        """Return the first source page number."""
        return self.source_spans[0].page_number

    @property
    def page_end(self) -> int:
        """Return the last source page number."""
        return self.source_spans[-1].page_number


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """Chunks and deterministic searchable-page coverage."""

    chunks: tuple[ChunkDraft, ...]
    coverage: ChunkCoverage


@dataclass(frozen=True, slots=True)
class _NaturalUnit:
    page: DocumentPage
    source_start: int
    source_end: int
    text: str
    token_spans: tuple[tuple[int, int], ...]
    heading: str | None

    @property
    def token_count(self) -> int:
        return len(self.token_spans)


@dataclass(frozen=True, slots=True)
class _Fragment:
    page: DocumentPage
    source_start: int
    source_end: int
    text: str
    token_count: int
    heading: str | None


def _page_boundary_is_textual(fragment: _Fragment, next_fragment: _Fragment) -> bool:
    """Permit cross-page joining only when the source has a token boundary."""
    if fragment.page.document_page_id == next_fragment.page.document_page_id:
        return True
    left = fragment.page.text[fragment.source_end - 1 : fragment.source_end]
    right = next_fragment.page.text[next_fragment.source_start : next_fragment.source_start + 1]
    return bool(left and right and (left.isspace() or right.isspace()))


def count_tokens(text: str) -> int:
    """Count deterministic lexical tokens without loading a model."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return sum(1 for _ in _TOKEN_PATTERN.finditer(text))


def _validate_source_version_sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("source version SHA-256 must be a lowercase hex digest")
    return value


def _heading_for_text(text: str) -> str | None:
    for line in text.strip().splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        match = _HEADING_PATTERN.fullmatch(candidate)
        return match.group(1).strip() if match else None
    return None


def _natural_units(page: DocumentPage) -> tuple[_NaturalUnit, ...]:
    matches = tuple(_PARAGRAPH_PATTERN.finditer(page.text))
    units: list[_NaturalUnit] = []
    for index, match in enumerate(matches):
        source_start = 0 if index == 0 else match.start()
        source_end = len(page.text) if index + 1 == len(matches) else matches[index + 1].start()
        text = page.text[source_start:source_end]
        token_spans = tuple((item.start(), item.end()) for item in _TOKEN_PATTERN.finditer(text))
        if token_spans:
            units.append(
                _NaturalUnit(
                    page=page,
                    source_start=source_start,
                    source_end=source_end,
                    text=text,
                    token_spans=token_spans,
                    heading=_heading_for_text(text),
                )
            )
    return tuple(units)


def _split_long_unit(unit: _NaturalUnit, config: RagIndexConfig) -> tuple[_Fragment, ...]:
    if unit.token_count <= config.chunk_hard_max_tokens:
        return (
            _Fragment(
                page=unit.page,
                source_start=unit.source_start,
                source_end=unit.source_end,
                text=unit.text,
                token_count=unit.token_count,
                heading=unit.heading,
            ),
        )

    window = min(config.chunk_target_tokens, config.chunk_hard_max_tokens)
    step = window - config.chunk_overlap_tokens
    fragments: list[_Fragment] = []
    start_index = 0
    while start_index < unit.token_count:
        end_index = min(start_index + window, unit.token_count)
        start_char = 0 if start_index == 0 else unit.token_spans[start_index][0]
        end_char = (
            len(unit.text) if end_index == unit.token_count else unit.token_spans[end_index][0]
        )
        text = unit.text[start_char:end_char]
        fragments.append(
            _Fragment(
                page=unit.page,
                source_start=unit.source_start + start_char,
                source_end=unit.source_start + end_char,
                text=text,
                token_count=count_tokens(text),
                heading=unit.heading,
            )
        )
        if end_index == unit.token_count:
            break
        start_index += step
    return tuple(fragments)


def _coalesce_fragments(fragments: tuple[_Fragment, ...]) -> tuple[_Fragment, ...]:
    merged: list[_Fragment] = []
    for fragment in fragments:
        if merged and merged[-1].page.document_page_id == fragment.page.document_page_id:
            previous = merged[-1]
            if previous.source_end == fragment.source_start:
                merged[-1] = _Fragment(
                    page=previous.page,
                    source_start=previous.source_start,
                    source_end=fragment.source_end,
                    text=previous.text + fragment.text,
                    token_count=previous.token_count + fragment.token_count,
                    heading=previous.heading,
                )
                continue
        merged.append(fragment)
    return tuple(merged)


def _chunk_key(
    config_fingerprint: str,
    document_version_id: int,
    source_version_sha256: str,
    ordinal: int,
    text: str,
    spans: tuple[ChunkSourceSpanDraft, ...],
) -> str:
    payload = {
        "config_fingerprint": config_fingerprint,
        "document_version_id": document_version_id,
        "source_version_sha256": source_version_sha256,
        "ordinal": ordinal,
        "source_spans": [
            {
                "document_page_id": span.document_page_id,
                "page_number": span.page_number,
                "source_char_start": span.source_char_start,
                "source_char_end": span.source_char_end,
                "chunk_char_start": span.chunk_char_start,
                "chunk_char_end": span.chunk_char_end,
            }
            for span in spans
        ],
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _draft_from_fragments(
    document_version_id: int,
    source_version_sha256: str,
    ordinal: int,
    fragments: tuple[_Fragment, ...],
    config_fingerprint: str,
) -> ChunkDraft:
    fragments = _coalesce_fragments(fragments)
    text_parts: list[str] = []
    spans: list[ChunkSourceSpanDraft] = []
    chunk_offset = 0
    for fragment in fragments:
        text_parts.append(fragment.text)
        end = chunk_offset + len(fragment.text)
        spans.append(
            ChunkSourceSpanDraft(
                document_page_id=fragment.page.document_page_id,
                page_number=fragment.page.page_number,
                source_char_start=fragment.source_start,
                source_char_end=fragment.source_end,
                chunk_char_start=chunk_offset,
                chunk_char_end=end,
            )
        )
        chunk_offset = end
    text = "".join(text_parts)
    draft_spans = tuple(spans)
    return ChunkDraft(
        document_version_id=document_version_id,
        ordinal=ordinal,
        text=text,
        token_count=count_tokens(text),
        source_spans=draft_spans,
        chunk_key=_chunk_key(
            config_fingerprint,
            document_version_id,
            source_version_sha256,
            ordinal,
            text,
            draft_spans,
        ),
        heading=next((fragment.heading for fragment in fragments if fragment.heading), None),
    )


class DeterministicChunker:
    """Chunk ordered pages using only versioned local deterministic rules."""

    def __init__(self, config: RagIndexConfig) -> None:
        if not isinstance(config, RagIndexConfig):
            raise ValueError("config must be a RagIndexConfig")
        self._config = config
        self._config_fingerprint = compute_config_fingerprint(config)

    def chunk(
        self,
        document_version_id: int,
        pages: Sequence[DocumentPageLike],
        source_version_sha256: str,
    ) -> tuple[ChunkDraft, ...]:
        """Return deterministic drafts for one document version."""
        if (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
        ):
            raise ValueError("document_version_id must be positive")
        source_version_sha256 = _validate_source_version_sha256(source_version_sha256)
        ordered_pages = tuple(pages)
        previous_page_number = 0
        seen_page_ids: set[int] = set()
        units: list[_NaturalUnit] = []
        for raw_page in ordered_pages:
            page = _coerce_page(raw_page)
            if page.document_version_id != document_version_id:
                raise ValueError("page document version does not match document version")
            if page.page_number <= previous_page_number:
                raise ValueError("pages must be ordered by page number")
            if page.document_page_id in seen_page_ids:
                raise ValueError("page identities must be unique")
            seen_page_ids.add(page.document_page_id)
            previous_page_number = page.page_number
            units.extend(_natural_units(page))

        drafts: list[ChunkDraft] = []
        current: list[_Fragment] = []
        current_tokens = 0
        ordinal = 1

        def emit_current() -> None:
            nonlocal ordinal, current, current_tokens
            if not current:
                return
            drafts.append(
                _draft_from_fragments(
                    document_version_id,
                    source_version_sha256,
                    ordinal,
                    tuple(current),
                    self._config_fingerprint,
                )
            )
            ordinal += 1
            current = []
            current_tokens = 0

        for unit in units:
            fragments = _split_long_unit(unit, self._config)
            if unit.token_count > self._config.chunk_hard_max_tokens:
                emit_current()
                for fragment in fragments:
                    drafts.append(
                        _draft_from_fragments(
                            document_version_id,
                            source_version_sha256,
                            ordinal,
                            (fragment,),
                            self._config_fingerprint,
                        )
                    )
                    ordinal += 1
                continue

            if current and not _page_boundary_is_textual(current[-1], fragments[0]):
                emit_current()
            if current and current_tokens + unit.token_count > self._config.chunk_hard_max_tokens:
                emit_current()
            current.extend(fragments)
            current_tokens += unit.token_count

        emit_current()
        return tuple(drafts)


def chunk_document(
    document_version_id: int,
    pages: Sequence[DocumentPageLike],
    config: RagIndexConfig,
    *,
    source_version_sha256: str,
) -> tuple[ChunkDraft, ...]:
    """Functional facade for deterministic document chunking."""
    return DeterministicChunker(config).chunk(
        document_version_id,
        pages,
        source_version_sha256,
    )


def chunk_document_result(
    document_version_id: int,
    pages: Sequence[DocumentPageLike],
    config: RagIndexConfig,
    *,
    source_version_sha256: str,
) -> ChunkingResult:
    """Return chunks with FULL/PARTIAL/NONE searchable-page coverage."""
    ordered_pages = tuple(_coerce_page(page) for page in pages)
    chunks = chunk_document(
        document_version_id,
        ordered_pages,
        config,
        source_version_sha256=source_version_sha256,
    )
    usable_pages = sum(1 for page in ordered_pages if page.text.strip())
    if not usable_pages:
        coverage = ChunkCoverage.NONE
    elif usable_pages == len(ordered_pages):
        coverage = ChunkCoverage.FULL
    else:
        coverage = ChunkCoverage.PARTIAL
    return ChunkingResult(chunks=chunks, coverage=coverage)


# Small aliases make the typed boundary discoverable without adding another
# public abstraction or changing the functional API.
Chunker = DeterministicChunker
