from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any, TypedDict, Unpack

import pytest

from aaos.documents.contracts import StructureUnitKind
from aaos.rag.chunking import ChunkCoverage, count_tokens
from aaos.rag.config import RagIndexConfig

SOURCE_SHA = "a" * 64


class _RagConfigValues(TypedDict):
    chunk_target_tokens: int
    chunk_preferred_min_tokens: int
    chunk_preferred_max_tokens: int
    chunk_hard_max_tokens: int
    chunk_overlap_tokens: int
    dense_candidate_limit: int
    lexical_candidate_limit: int
    rrf_k: int
    max_evidence_chunks: int
    max_evidence_tokens: int
    chunker_version: str
    lexical_normalizer_version: str
    sufficiency_policy_version: str
    embedding_model_identifier: str
    embedding_model_revision: str
    embedding_dimensions: int


class _RagConfigOverrides(TypedDict, total=False):
    chunk_target_tokens: int
    chunk_preferred_min_tokens: int
    chunk_preferred_max_tokens: int
    chunk_hard_max_tokens: int
    chunk_overlap_tokens: int
    dense_candidate_limit: int
    lexical_candidate_limit: int
    rrf_k: int
    max_evidence_chunks: int
    max_evidence_tokens: int
    chunker_version: str
    lexical_normalizer_version: str
    sufficiency_policy_version: str
    embedding_model_identifier: str
    embedding_model_revision: str
    embedding_dimensions: int


def subject() -> ModuleType:
    return import_module("aaos.rag.structural_chunking")


def make_config(**changes: Unpack[_RagConfigOverrides]) -> RagIndexConfig:
    values: _RagConfigValues = {
        "chunk_target_tokens": 5,
        "chunk_preferred_min_tokens": 3,
        "chunk_preferred_max_tokens": 6,
        "chunk_hard_max_tokens": 8,
        "chunk_overlap_tokens": 1,
        "dense_candidate_limit": 30,
        "lexical_candidate_limit": 30,
        "rrf_k": 60,
        "max_evidence_chunks": 6,
        "max_evidence_tokens": 2400,
        "chunker_version": "deterministic-v1",
        "lexical_normalizer_version": "lexical-v1",
        "sufficiency_policy_version": "conservative-v1",
        "embedding_model_identifier": "BAAI/bge-m3",
        "embedding_model_revision": "v1",
        "embedding_dimensions": 1024,
    }
    values.update(changes)
    return RagIndexConfig(**values)


def unit(
    unit_id: int,
    version_id: int,
    ordinal: int,
    kind: StructureUnitKind,
    text: str,
    *,
    heading_level: int | None = None,
    table_index: int | None = None,
    row_index: int | None = None,
    column_index: int | None = None,
) -> Any:
    return subject().DocumentStructureUnit(
        document_structure_unit_id=unit_id,
        document_version_id=version_id,
        ordinal=ordinal,
        kind=kind,
        text=text,
        heading_level=heading_level,
        table_index=table_index,
        row_index=row_index,
        column_index=column_index,
    )


def chunk_structure(
    document_version_id: int,
    units: tuple[Any, ...],
    config: RagIndexConfig,
    *,
    source_version_sha256: str = SOURCE_SHA,
) -> tuple[Any, ...]:
    chunks = subject().chunk_structure_document(
        document_version_id,
        units,
        config,
        source_version_sha256=source_version_sha256,
    )
    assert isinstance(chunks, tuple)
    return chunks


def assert_source_mapping(
    chunk: Any,
    units: tuple[Any, ...],
) -> None:
    by_id = {item.document_structure_unit_id: item for item in units}

    for span in chunk.source_spans:
        source = by_id[span.document_structure_unit_id]

        assert (
            source.text[span.source_char_start : span.source_char_end]
            == chunk.text[span.chunk_char_start : span.chunk_char_end]
        )


def test_structural_chunk_surface_is_explicit_and_page_free() -> None:
    module = subject()

    source = unit(
        101,
        7,
        1,
        StructureUnitKind.PARAGRAPH,
        "alpha",
    )
    span = module.StructureChunkSourceSpanDraft(
        document_structure_unit_id=101,
        source_char_start=0,
        source_char_end=5,
        chunk_char_start=0,
        chunk_char_end=5,
    )
    draft = module.StructureChunkDraft(
        document_version_id=7,
        ordinal=1,
        text="alpha",
        token_count=1,
        source_spans=(span,),
        chunk_key="a" * 64,
        heading=None,
    )

    assert source.document_structure_unit_id == 101
    assert draft.exact_text == "alpha"
    assert draft.text_sha256
    assert draft.provenance_mode == "STRUCTURE"
    assert draft.page_start is None
    assert draft.page_end is None
    assert draft.source_spans == (span,)

    with pytest.raises(AttributeError):
        draft.text = "changed"


def test_heading_and_paragraphs_render_with_exact_unit_provenance() -> None:
    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.HEADING,
            "Neural Networks",
            heading_level=1,
        ),
        unit(
            102,
            7,
            2,
            StructureUnitKind.PARAGRAPH,
            "A neural network learns patterns.",
        ),
        unit(
            103,
            7,
            3,
            StructureUnitKind.PARAGRAPH,
            "Hidden layers transform features.",
        ),
    )

    chunks = chunk_structure(
        7,
        units,
        make_config(
            chunk_target_tokens=20,
            chunk_preferred_max_tokens=24,
            chunk_hard_max_tokens=32,
        ),
    )

    assert len(chunks) == 1

    chunk = chunks[0]
    assert chunk.text == (
        "Neural Networks\n\nA neural network learns patterns.\n\nHidden layers transform features."
    )
    assert chunk.heading == "Neural Networks"
    assert chunk.provenance_mode == "STRUCTURE"
    assert chunk.page_start is None
    assert chunk.page_end is None

    assert [span.document_structure_unit_id for span in chunk.source_spans] == [101, 102, 103]

    assert_source_mapping(chunk, units)


def test_new_heading_starts_a_new_structural_chunk() -> None:
    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.HEADING,
            "First",
            heading_level=1,
        ),
        unit(
            102,
            7,
            2,
            StructureUnitKind.PARAGRAPH,
            "alpha beta",
        ),
        unit(
            103,
            7,
            3,
            StructureUnitKind.HEADING,
            "Second",
            heading_level=2,
        ),
        unit(
            104,
            7,
            4,
            StructureUnitKind.PARAGRAPH,
            "gamma delta",
        ),
    )

    chunks = chunk_structure(
        7,
        units,
        make_config(
            chunk_target_tokens=20,
            chunk_preferred_max_tokens=24,
            chunk_hard_max_tokens=32,
        ),
    )

    assert [chunk.text for chunk in chunks] == [
        "First\n\nalpha beta",
        "Second\n\ngamma delta",
    ]
    assert [chunk.heading for chunk in chunks] == [
        "First",
        "Second",
    ]

    assert_source_mapping(chunks[0], units)
    assert_source_mapping(chunks[1], units)


def test_table_cells_preserve_row_major_structure_and_unit_spans() -> None:
    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.TABLE_CELL,
            "A",
            table_index=0,
            row_index=0,
            column_index=0,
        ),
        unit(
            102,
            7,
            2,
            StructureUnitKind.TABLE_CELL,
            "B",
            table_index=0,
            row_index=0,
            column_index=1,
        ),
        unit(
            103,
            7,
            3,
            StructureUnitKind.TABLE_CELL,
            "C",
            table_index=0,
            row_index=1,
            column_index=0,
        ),
        unit(
            104,
            7,
            4,
            StructureUnitKind.TABLE_CELL,
            "D",
            table_index=0,
            row_index=1,
            column_index=1,
        ),
    )

    chunks = chunk_structure(
        7,
        units,
        make_config(chunk_hard_max_tokens=16),
    )

    assert len(chunks) == 1
    assert chunks[0].text == "A\tB\nC\tD"
    assert [span.document_structure_unit_id for span in chunks[0].source_spans] == [
        101,
        102,
        103,
        104,
    ]

    assert_source_mapping(chunks[0], units)


def test_long_structural_unit_uses_bounded_overlap_only_when_split() -> None:
    text = " ".join(f"term{index}" for index in range(17))
    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.PARAGRAPH,
            text,
        ),
    )
    config = make_config()

    chunks = chunk_structure(7, units, config)

    assert len(chunks) >= 3
    assert all(chunk.token_count <= config.chunk_hard_max_tokens for chunk in chunks)
    assert all(chunk.token_count == count_tokens(chunk.text) for chunk in chunks)

    for previous, current in zip(chunks, chunks[1:]):
        assert set(previous.text.split()) & set(current.text.split())

    assert chunks[0].source_spans[0].source_char_start == 0
    assert chunks[-1].source_spans[-1].source_char_end == len(text)
    assert chunks == chunk_structure(7, units, config)

    for chunk in chunks:
        assert_source_mapping(chunk, units)


def test_blank_structure_units_are_skipped_with_deterministic_coverage() -> None:
    module = subject()

    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.PARAGRAPH,
            "   ",
        ),
        unit(
            102,
            7,
            2,
            StructureUnitKind.PARAGRAPH,
            "usable text",
        ),
    )
    empty_units = (
        unit(
            201,
            8,
            1,
            StructureUnitKind.PARAGRAPH,
            " \t ",
        ),
    )

    result = module.chunk_structure_document_result(
        7,
        units,
        make_config(),
        source_version_sha256=SOURCE_SHA,
    )
    empty_result = module.chunk_structure_document_result(
        8,
        empty_units,
        make_config(),
        source_version_sha256=SOURCE_SHA,
    )

    assert result.coverage is ChunkCoverage.PARTIAL
    assert [chunk.text for chunk in result.chunks] == ["usable text"]

    assert empty_result.coverage is ChunkCoverage.NONE
    assert empty_result.chunks == ()


def test_structural_units_fail_closed_on_order_identity_and_version() -> None:
    config = make_config()

    with pytest.raises(ValueError, match="ordered"):
        chunk_structure(
            7,
            (
                unit(
                    101,
                    7,
                    2,
                    StructureUnitKind.PARAGRAPH,
                    "later",
                ),
                unit(
                    102,
                    7,
                    1,
                    StructureUnitKind.PARAGRAPH,
                    "earlier",
                ),
            ),
            config,
        )

    with pytest.raises(ValueError, match="document version"):
        chunk_structure(
            7,
            (
                unit(
                    101,
                    8,
                    1,
                    StructureUnitKind.PARAGRAPH,
                    "wrong",
                ),
            ),
            config,
        )

    with pytest.raises(ValueError, match="identities"):
        chunk_structure(
            7,
            (
                unit(
                    101,
                    7,
                    1,
                    StructureUnitKind.PARAGRAPH,
                    "one",
                ),
                unit(
                    101,
                    7,
                    2,
                    StructureUnitKind.PARAGRAPH,
                    "two",
                ),
            ),
            config,
        )


def test_structural_chunk_keys_bind_version_source_and_config() -> None:
    units = (
        unit(
            101,
            7,
            1,
            StructureUnitKind.PARAGRAPH,
            "stable text",
        ),
    )

    base = chunk_structure(
        7,
        units,
        make_config(),
        source_version_sha256=SOURCE_SHA,
    )

    assert base == chunk_structure(
        7,
        units,
        make_config(),
        source_version_sha256=SOURCE_SHA,
    )

    changed_sha = chunk_structure(
        7,
        units,
        make_config(),
        source_version_sha256="b" * 64,
    )

    changed_config = chunk_structure(
        7,
        units,
        make_config(chunker_version="deterministic-v2"),
        source_version_sha256=SOURCE_SHA,
    )

    changed_version = chunk_structure(
        8,
        (
            unit(
                101,
                8,
                1,
                StructureUnitKind.PARAGRAPH,
                "stable text",
            ),
        ),
        make_config(),
        source_version_sha256=SOURCE_SHA,
    )

    assert base[0].chunk_key != changed_sha[0].chunk_key
    assert base[0].chunk_key != changed_config[0].chunk_key
    assert base[0].chunk_key != changed_version[0].chunk_key
