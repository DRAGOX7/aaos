"""Deterministic chunking for STRUCTURE-provenance documents.

This module is intentionally separate from the historical PAGE/PDF chunker.
Structural chunks preserve DocumentStructureUnit identities and never invent
page numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, Sequence

from aaos.documents.contracts import StructureUnitKind

from .chunking import ChunkCoverage, count_tokens
from .config import RagIndexConfig, compute_config_fingerprint

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)
_CHUNK_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class DocumentStructureUnit:
    """Immutable structural text with stable persisted provenance identity."""

    document_structure_unit_id: int
    document_version_id: int
    ordinal: int
    kind: StructureUnitKind
    text: str
    heading_level: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None

    def __post_init__(self) -> None:
        _positive_int(
            self.document_structure_unit_id,
            "document_structure_unit_id",
        )
        _positive_int(
            self.document_version_id,
            "document_version_id",
        )
        _positive_int(self.ordinal, "ordinal")

        try:
            kind = StructureUnitKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ValueError("unsupported structure unit kind") from error

        object.__setattr__(self, "kind", kind)

        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

        if kind is StructureUnitKind.HEADING:
            if (
                isinstance(self.heading_level, bool)
                or not isinstance(self.heading_level, int)
                or not 1 <= self.heading_level <= 9
            ):
                raise ValueError("heading_level must be between 1 and 9")
            if any(
                value is not None
                for value in (
                    self.table_index,
                    self.row_index,
                    self.column_index,
                )
            ):
                raise ValueError("heading units cannot have table coordinates")
            return

        if kind is StructureUnitKind.PARAGRAPH:
            if self.heading_level is not None or any(
                value is not None
                for value in (
                    self.table_index,
                    self.row_index,
                    self.column_index,
                )
            ):
                raise ValueError("paragraph units cannot have heading or table metadata")
            return

        if self.heading_level is not None:
            raise ValueError("table cells cannot have heading metadata")

        for name in (
            "table_index",
            "row_index",
            "column_index",
        ):
            value = getattr(self, name)
            if value is None:
                raise ValueError("table cells require complete coordinates")
            _nonnegative_int(value, name)


class DocumentStructureUnitLike(Protocol):
    @property
    def document_structure_unit_id(self) -> int: ...

    @property
    def document_version_id(self) -> int: ...

    @property
    def ordinal(self) -> int: ...

    @property
    def kind(self) -> StructureUnitKind | str: ...

    @property
    def text(self) -> str: ...

    @property
    def heading_level(self) -> int | None: ...

    @property
    def table_index(self) -> int | None: ...

    @property
    def row_index(self) -> int | None: ...

    @property
    def column_index(self) -> int | None: ...


def _coerce_unit(
    value: DocumentStructureUnitLike,
) -> DocumentStructureUnit:
    if isinstance(value, DocumentStructureUnit):
        return value

    return DocumentStructureUnit(
        document_structure_unit_id=value.document_structure_unit_id,
        document_version_id=value.document_version_id,
        ordinal=value.ordinal,
        kind=StructureUnitKind(value.kind),
        text=value.text,
        heading_level=value.heading_level,
        table_index=value.table_index,
        row_index=value.row_index,
        column_index=value.column_index,
    )


@dataclass(frozen=True, slots=True)
class StructureChunkSourceSpanDraft:
    """Exact half-open mapping from one structure unit into a chunk."""

    document_structure_unit_id: int
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int

    def __post_init__(self) -> None:
        _positive_int(
            self.document_structure_unit_id,
            "document_structure_unit_id",
        )

        for name in (
            "source_char_start",
            "source_char_end",
            "chunk_char_start",
            "chunk_char_end",
        ):
            _nonnegative_int(getattr(self, name), name)

        if self.source_char_end <= self.source_char_start:
            raise ValueError("source_char span must be monotonic and non-empty")
        if self.chunk_char_end <= self.chunk_char_start:
            raise ValueError("chunk_char span must be monotonic and non-empty")


@dataclass(frozen=True, slots=True)
class StructureChunkDraft:
    """One immutable page-less STRUCTURE chunk ready for persistence."""

    document_version_id: int
    ordinal: int
    text: str
    token_count: int
    source_spans: tuple[StructureChunkSourceSpanDraft, ...]
    chunk_key: str
    heading: str | None

    def __post_init__(self) -> None:
        _positive_int(
            self.document_version_id,
            "document_version_id",
        )
        _positive_int(self.ordinal, "ordinal")

        if not isinstance(self.text, str) or not self.text:
            raise ValueError("chunk text must be non-empty")

        _positive_int(self.token_count, "token_count")

        if not isinstance(self.source_spans, tuple):
            raise ValueError("source_spans must be an immutable tuple")
        if not self.source_spans:
            raise ValueError("chunk must have source spans")
        if not all(
            isinstance(
                span,
                StructureChunkSourceSpanDraft,
            )
            for span in self.source_spans
        ):
            raise ValueError("source_spans must contain structural source spans")

        if (
            not isinstance(self.chunk_key, str)
            or _CHUNK_KEY_PATTERN.fullmatch(self.chunk_key) is None
        ):
            raise ValueError("chunk_key must be a lowercase SHA-256 hex digest")

        if self.heading is not None and (
            not isinstance(self.heading, str) or not self.heading.strip()
        ):
            raise ValueError("heading must be non-blank text when present")

    @property
    def exact_text(self) -> str:
        return self.text

    @property
    def spans(
        self,
    ) -> tuple[StructureChunkSourceSpanDraft, ...]:
        return self.source_spans

    @property
    def text_sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def provenance_mode(self) -> str:
        return "STRUCTURE"

    @property
    def page_start(self) -> None:
        return None

    @property
    def page_end(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class StructureChunkingResult:
    chunks: tuple[StructureChunkDraft, ...]
    coverage: ChunkCoverage


@dataclass(frozen=True, slots=True)
class _Fragment:
    unit: DocumentStructureUnit
    source_start: int
    source_end: int
    text: str
    token_count: int


def _validate_source_version_sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("source version SHA-256 must be a lowercase hex digest")
    return value


def _split_long_unit(
    unit: DocumentStructureUnit,
    config: RagIndexConfig,
) -> tuple[_Fragment, ...]:
    token_spans = tuple(
        (match.start(), match.end()) for match in _TOKEN_PATTERN.finditer(unit.text)
    )

    token_count = len(token_spans)
    if token_count <= config.chunk_hard_max_tokens:
        return (
            _Fragment(
                unit=unit,
                source_start=0,
                source_end=len(unit.text),
                text=unit.text,
                token_count=token_count,
            ),
        )

    window = min(
        config.chunk_target_tokens,
        config.chunk_hard_max_tokens,
    )
    step = window - config.chunk_overlap_tokens

    fragments: list[_Fragment] = []
    start_index = 0

    while start_index < token_count:
        end_index = min(
            start_index + window,
            token_count,
        )

        start_char = 0 if start_index == 0 else token_spans[start_index][0]
        end_char = len(unit.text) if end_index == token_count else token_spans[end_index][0]

        fragment_text = unit.text[start_char:end_char]

        fragments.append(
            _Fragment(
                unit=unit,
                source_start=start_char,
                source_end=end_char,
                text=fragment_text,
                token_count=count_tokens(fragment_text),
            )
        )

        if end_index == token_count:
            break

        start_index += step

    return tuple(fragments)


def _separator(
    previous: _Fragment,
    current: _Fragment,
) -> str:
    left = previous.unit
    right = current.unit

    if left.kind is StructureUnitKind.TABLE_CELL and right.kind is StructureUnitKind.TABLE_CELL:
        if left.table_index == right.table_index:
            if left.row_index == right.row_index:
                return "\t"
            return "\n"

    return "\n\n"


def _chunk_key(
    config_fingerprint: str,
    document_version_id: int,
    source_version_sha256: str,
    ordinal: int,
    text: str,
    spans: tuple[StructureChunkSourceSpanDraft, ...],
) -> str:
    payload = {
        "config_fingerprint": config_fingerprint,
        "document_version_id": document_version_id,
        "ordinal": ordinal,
        "provenance_mode": "STRUCTURE",
        "source_version_sha256": source_version_sha256,
        "source_spans": [
            {
                "document_structure_unit_id": (span.document_structure_unit_id),
                "source_char_start": span.source_char_start,
                "source_char_end": span.source_char_end,
                "chunk_char_start": span.chunk_char_start,
                "chunk_char_end": span.chunk_char_end,
            }
            for span in spans
        ],
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
    }

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _draft_from_fragments(
    document_version_id: int,
    source_version_sha256: str,
    ordinal: int,
    fragments: tuple[_Fragment, ...],
    config_fingerprint: str,
    heading: str | None,
) -> StructureChunkDraft:
    text_parts: list[str] = []
    spans: list[StructureChunkSourceSpanDraft] = []
    chunk_offset = 0
    previous: _Fragment | None = None

    for fragment in fragments:
        if previous is not None:
            separator = _separator(previous, fragment)
            text_parts.append(separator)
            chunk_offset += len(separator)

        text_parts.append(fragment.text)

        chunk_end = chunk_offset + len(fragment.text)
        spans.append(
            StructureChunkSourceSpanDraft(
                document_structure_unit_id=(fragment.unit.document_structure_unit_id),
                source_char_start=fragment.source_start,
                source_char_end=fragment.source_end,
                chunk_char_start=chunk_offset,
                chunk_char_end=chunk_end,
            )
        )

        chunk_offset = chunk_end
        previous = fragment

    text = "".join(text_parts)
    draft_spans = tuple(spans)

    return StructureChunkDraft(
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
        heading=heading,
    )


class DeterministicStructureChunker:
    """Chunk ordered structural units using deterministic local rules."""

    def __init__(self, config: RagIndexConfig) -> None:
        if not isinstance(config, RagIndexConfig):
            raise ValueError("config must be a RagIndexConfig")

        self._config = config
        self._config_fingerprint = compute_config_fingerprint(config)

    @property
    def config(self) -> RagIndexConfig:
        return self._config

    @property
    def config_fingerprint(self) -> str:
        return self._config_fingerprint

    def chunk(
        self,
        document_version_id: int,
        units: Sequence[DocumentStructureUnitLike],
        source_version_sha256: str,
    ) -> tuple[StructureChunkDraft, ...]:
        _positive_int(
            document_version_id,
            "document_version_id",
        )
        source_version_sha256 = _validate_source_version_sha256(source_version_sha256)

        ordered_units = tuple(_coerce_unit(unit) for unit in units)

        previous_ordinal = 0
        seen_unit_ids: set[int] = set()

        for unit in ordered_units:
            if unit.document_version_id != document_version_id:
                raise ValueError("structure unit document version does not match document version")

            if unit.ordinal <= previous_ordinal:
                raise ValueError("structure units must be ordered by ordinal")

            if unit.document_structure_unit_id in seen_unit_ids:
                raise ValueError("structure unit identities must be unique")

            seen_unit_ids.add(unit.document_structure_unit_id)
            previous_ordinal = unit.ordinal

        drafts: list[StructureChunkDraft] = []
        current: list[_Fragment] = []
        current_tokens = 0
        ordinal = 1
        active_heading: str | None = None

        def emit_current() -> None:
            nonlocal ordinal
            nonlocal current
            nonlocal current_tokens

            if not current:
                return

            drafts.append(
                _draft_from_fragments(
                    document_version_id,
                    source_version_sha256,
                    ordinal,
                    tuple(current),
                    self._config_fingerprint,
                    active_heading,
                )
            )

            ordinal += 1
            current = []
            current_tokens = 0

        for unit in ordered_units:
            if not unit.text.strip():
                continue

            if unit.kind is StructureUnitKind.HEADING:
                emit_current()
                active_heading = unit.text.strip()

            fragments = _split_long_unit(
                unit,
                self._config,
            )
            unit_tokens = count_tokens(unit.text)

            if unit_tokens > self._config.chunk_hard_max_tokens:
                emit_current()

                for fragment in fragments:
                    drafts.append(
                        _draft_from_fragments(
                            document_version_id,
                            source_version_sha256,
                            ordinal,
                            (fragment,),
                            self._config_fingerprint,
                            active_heading,
                        )
                    )
                    ordinal += 1

                continue

            if current and current_tokens + unit_tokens > self._config.chunk_hard_max_tokens:
                emit_current()

            current.extend(fragments)
            current_tokens += unit_tokens

        emit_current()
        return tuple(drafts)


def chunk_structure_document(
    document_version_id: int,
    units: Sequence[DocumentStructureUnitLike],
    config: RagIndexConfig,
    *,
    source_version_sha256: str,
) -> tuple[StructureChunkDraft, ...]:
    """Return deterministic page-less chunks for one STRUCTURE document."""

    return DeterministicStructureChunker(config).chunk(
        document_version_id,
        units,
        source_version_sha256,
    )


def chunk_structure_document_result(
    document_version_id: int,
    units: Sequence[DocumentStructureUnitLike],
    config: RagIndexConfig,
    *,
    source_version_sha256: str,
) -> StructureChunkingResult:
    """Return chunks with deterministic structural-unit coverage."""

    ordered_units = tuple(_coerce_unit(unit) for unit in units)

    chunks = chunk_structure_document(
        document_version_id,
        ordered_units,
        config,
        source_version_sha256=source_version_sha256,
    )

    usable_units = sum(1 for unit in ordered_units if unit.text.strip())

    if not usable_units:
        coverage = ChunkCoverage.NONE
    elif usable_units == len(ordered_units):
        coverage = ChunkCoverage.FULL
    else:
        coverage = ChunkCoverage.PARTIAL

    return StructureChunkingResult(
        chunks=chunks,
        coverage=coverage,
    )


StructureChunker = DeterministicStructureChunker


__all__ = [
    "DeterministicStructureChunker",
    "DocumentStructureUnit",
    "DocumentStructureUnitLike",
    "StructureChunkDraft",
    "StructureChunkSourceSpanDraft",
    "StructureChunker",
    "StructureChunkingResult",
    "chunk_structure_document",
    "chunk_structure_document_result",
]
