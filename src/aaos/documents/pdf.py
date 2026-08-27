"""Deterministic embedded-text PDF validation and extraction."""

from __future__ import annotations

import math
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from pypdfium2 import raw

from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    DocumentIngestionFailure,
    ExtractedPage,
    ExtractionStatus,
    IngestionErrorCode,
    ParsedPdf,
    TextSource,
)


def _failure(code: IngestionErrorCode) -> DocumentIngestionFailure:
    return DocumentIngestionFailure(code)


class PdfProcessor:
    """Validate a PDF and expose only application-owned page contracts."""

    def process(self, body: bytes) -> ParsedPdf:
        if not body or len(body) > MAX_PDF_BYTES:
            raise _failure(IngestionErrorCode.INVALID_PDF)

        try:
            document = pdfium.PdfDocument(body)
        except pdfium.PdfiumError as error:
            if error.err_code == raw.FPDF_ERR_PASSWORD:
                raise _failure(IngestionErrorCode.PDF_ENCRYPTED) from None
            raise _failure(IngestionErrorCode.INVALID_PDF) from None
        except (OSError, RuntimeError, TypeError, ValueError):
            raise _failure(IngestionErrorCode.INVALID_PDF) from None

        try:
            with document:
                page_count = len(document)
                if page_count < 1:
                    raise _failure(IngestionErrorCode.PDF_PARSE_FAILED)
                pages = tuple(
                    _extract_page(document[index], page_number=index + 1)
                    for index in range(page_count)
                )
        except DocumentIngestionFailure:
            raise
        except (OSError, RuntimeError, TypeError, ValueError):
            raise _failure(IngestionErrorCode.PDF_PARSE_FAILED) from None

        if all(page.text.strip() for page in pages):
            extraction_status = ExtractionStatus.SUCCESS
        elif any(page.text.strip() for page in pages):
            extraction_status = ExtractionStatus.PARTIAL
        else:
            extraction_status = ExtractionStatus.NO_TEXT
        return ParsedPdf(
            page_count=page_count,
            extraction_status=extraction_status,
            pages=pages,
        )


def _extract_page(page: Any, *, page_number: int) -> ExtractedPage:
    text_page: Any | None = None
    try:
        width, height = page.get_size()
        width_points = float(width)
        height_points = float(height)
        if (
            not math.isfinite(width_points)
            or not math.isfinite(height_points)
            or width_points <= 0
            or height_points <= 0
        ):
            raise _failure(IngestionErrorCode.PDF_PARSE_FAILED)
        text_page = page.get_textpage()
        text = text_page.get_text_bounded()
        if not isinstance(text, str):
            raise _failure(IngestionErrorCode.PDF_PARSE_FAILED)
        return ExtractedPage(
            page_number=page_number,
            text=text,
            text_source=TextSource.EMBEDDED_PDF,
            width_points=width_points,
            height_points=height_points,
        )
    except DocumentIngestionFailure:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _failure(IngestionErrorCode.PDF_PARSE_FAILED) from None
    finally:
        if text_page is not None:
            try:
                text_page.close()
            except (OSError, RuntimeError):
                pass
        try:
            page.close()
        except (OSError, RuntimeError):
            pass
