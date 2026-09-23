from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields

import pytest
from docx_fixtures import (
    all_heading_levels_docx,
    blank_paragraph_docx,
    merged_cell_docx,
    mixed_structure_docx,
    nested_table_docx,
    omitted_leading_cell_docx,
    vertical_merged_cell_docx,
)

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    ExtractedStructureUnit,
    IngestionErrorCode,
    ParsedDocx,
    StructureUnitKind,
)
from aaos.documents.docx import DocxProcessor


def _shape(unit: ExtractedStructureUnit) -> tuple[object, ...]:
    return (
        unit.ordinal,
        unit.kind,
        unit.text,
        unit.heading_level,
        unit.table_index,
        unit.row_index,
        unit.column_index,
    )


def test_docx_contract_has_no_page_semantics() -> None:
    assert {field.name for field in fields(ExtractedStructureUnit)} == {
        "ordinal",
        "kind",
        "text",
        "heading_level",
        "table_index",
        "row_index",
        "column_index",
    }
    assert {field.name for field in fields(ParsedDocx)} == {"units"}
    assert {member.value for member in StructureUnitKind} == {
        "HEADING",
        "PARAGRAPH",
        "TABLE_CELL",
    }


def test_docx_parser_preserves_interleaved_structure_and_exact_text() -> None:
    parsed = DocxProcessor().process(mixed_structure_docx())

    assert [_shape(unit) for unit in parsed.units] == [
        (1, StructureUnitKind.HEADING, "Course Overview", 1, None, None, None),
        (
            2,
            StructureUnitKind.PARAGRAPH,
            "تعلم الآلة — Machine Learning",
            None,
            None,
            None,
            None,
        ),
        (3, StructureUnitKind.TABLE_CELL, "A1", None, 0, 0, 0),
        (4, StructureUnitKind.TABLE_CELL, "B1", None, 0, 0, 1),
        (5, StructureUnitKind.TABLE_CELL, "A2\ncontinued", None, 0, 1, 0),
        (6, StructureUnitKind.TABLE_CELL, "", None, 0, 1, 1),
        (
            7,
            StructureUnitKind.PARAGRAPH,
            "Between tables",
            None,
            None,
            None,
            None,
        ),
        (8, StructureUnitKind.TABLE_CELL, "C1", None, 1, 0, 0),
        (9, StructureUnitKind.TABLE_CELL, "D1", None, 1, 0, 1),
        (10, StructureUnitKind.HEADING, "Details", 3, None, None, None),
    ]


def test_docx_parser_recognizes_only_heading_levels_one_through_nine() -> None:
    parsed = DocxProcessor().process(all_heading_levels_docx())

    assert [unit.ordinal for unit in parsed.units] == list(range(1, 10))
    assert [unit.kind for unit in parsed.units] == [StructureUnitKind.HEADING] * 9
    assert [unit.heading_level for unit in parsed.units] == list(range(1, 10))


def test_docx_parser_retains_blank_paragraph_structure() -> None:
    parsed = DocxProcessor().process(blank_paragraph_docx())

    assert len(parsed.units) == 1
    assert _shape(parsed.units[0]) == (
        1,
        StructureUnitKind.PARAGRAPH,
        "",
        None,
        None,
        None,
        None,
    )


def test_docx_parser_is_deterministic_for_identical_bytes() -> None:
    body = mixed_structure_docx()
    processor = DocxProcessor()

    assert processor.process(body) == processor.process(body)


@pytest.mark.parametrize("body", (b"", b"not-a-docx", b"%PDF-1.7"))
def test_docx_parser_rejects_non_docx_bytes(body: bytes) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        DocxProcessor().process(body)

    assert caught.value.code is IngestionErrorCode.INVALID_DOCX


def test_docx_parser_preserves_grid_column_for_omitted_leading_cell() -> None:
    parsed = DocxProcessor().process(omitted_leading_cell_docx())

    assert [_shape(unit) for unit in parsed.units] == [
        (1, StructureUnitKind.TABLE_CELL, "second", None, 0, 0, 1),
        (2, StructureUnitKind.TABLE_CELL, "third", None, 0, 0, 2),
    ]


@pytest.mark.parametrize(
    "fixture",
    (
        nested_table_docx,
        merged_cell_docx,
        vertical_merged_cell_docx,
    ),
)
def test_docx_parser_rejects_unsupported_table_structure_without_silent_loss(
    fixture: Callable[[], bytes],
) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        DocxProcessor().process(fixture())

    assert caught.value.code is IngestionErrorCode.UNSUPPORTED_DOCX_STRUCTURE
