"""Deterministic PDF processing contract tests for Phase 3 Task 5."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pdf_fixtures import (
    all_empty_pdf,
    html_as_pdf,
    malformed_pdf,
    mixed_text_empty_pdf,
    mixed_unicode_pdf,
    password_required_pdf,
    valid_multipage_pdf,
    valid_one_page_pdf,
    zero_page_pdf,
)

from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    DocumentIngestionFailure,
    ExtractionStatus,
    IngestionErrorCode,
    TextSource,
)
from aaos.documents.pdf import PdfProcessor


def assert_failure(
    caught: pytest.ExceptionInfo[DocumentIngestionFailure], code: IngestionErrorCode
) -> None:
    assert caught.value.code is code
    assert str(caught.value) == "document ingestion failed"


def test_valid_one_page_extracts_contract_fields() -> None:
    parsed = PdfProcessor().process(valid_one_page_pdf())

    assert parsed.page_count == 1
    assert parsed.extraction_status is ExtractionStatus.SUCCESS
    page = parsed.pages[0]
    assert page.page_number == 1
    assert page.text.strip() == "Hello AAOS"
    assert page.text_source is TextSource.EMBEDDED_PDF
    assert math.isfinite(page.width_points) and page.width_points > 0
    assert math.isfinite(page.height_points) and page.height_points > 0


def test_mixed_arabic_and_english_text_is_preserved() -> None:
    page = PdfProcessor().process(mixed_unicode_pdf()).pages[0]

    assert all(character in page.text for character in "مرحبا")
    assert "AAOS" in page.text


def test_multipage_preserves_order_and_one_based_numbers() -> None:
    parsed = PdfProcessor().process(valid_multipage_pdf())

    assert parsed.page_count == 3
    assert [page.page_number for page in parsed.pages] == [1, 2, 3]
    assert [page.text.strip() for page in parsed.pages] == [
        "Page one",
        "Page two",
        "Page three",
    ]


def test_mixed_text_and_empty_page_is_partial_and_preserved() -> None:
    parsed = PdfProcessor().process(mixed_text_empty_pdf())

    assert parsed.page_count == 2
    assert parsed.extraction_status is ExtractionStatus.PARTIAL
    assert parsed.pages[1].text.strip() == ""


def test_all_empty_pages_are_no_text_and_preserved() -> None:
    parsed = PdfProcessor().process(all_empty_pdf())

    assert parsed.page_count == 2
    assert parsed.extraction_status is ExtractionStatus.NO_TEXT
    assert len(parsed.pages) == 2


def test_zero_page_document_is_rejected_as_invalid_initial_load() -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(zero_page_pdf())

    assert_failure(caught, IngestionErrorCode.INVALID_PDF)


@pytest.mark.parametrize("body", (b"", b"x" * (MAX_PDF_BYTES + 1)), ids=("empty", "oversized"))
def test_empty_and_oversized_body_rejected_before_processing(body: bytes) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(body)

    assert_failure(caught, IngestionErrorCode.INVALID_PDF)


@pytest.mark.parametrize("body", (malformed_pdf(), html_as_pdf()), ids=("malformed", "html"))
def test_invalid_initial_load_maps_to_invalid_pdf(body: bytes) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(body)

    assert_failure(caught, IngestionErrorCode.INVALID_PDF)


def test_password_required_initial_load_maps_to_encrypted() -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(password_required_pdf())

    assert_failure(caught, IngestionErrorCode.PDF_ENCRYPTED)


def test_post_open_processing_failure_maps_to_parse_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_open(*args: object, **kwargs: object) -> object:
        raise RuntimeError("native processing detail must not escape")

    monkeypatch.setattr("aaos.documents.pdf._extract_page", fail_after_open)

    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(valid_one_page_pdf())

    assert_failure(caught, IngestionErrorCode.PDF_PARSE_FAILED)


def test_native_error_details_and_rendering_artifacts_are_not_exposed(tmp_path: Path) -> None:
    with pytest.raises(DocumentIngestionFailure) as caught:
        PdfProcessor().process(malformed_pdf())

    assert "native" not in str(caught.value)
    assert not list(tmp_path.iterdir())
