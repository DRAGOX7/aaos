"""Deterministic embedded-text PDF validation and extraction."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
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

_ARABIC = re.compile("[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_WORD = re.compile(r"\S+")


def _failure(code: IngestionErrorCode) -> DocumentIngestionFailure:
    return DocumentIngestionFailure(code)


def reading_order_text(text: str, x_centres: Sequence[float]) -> str:
    """Return page text with Arabic lines in reading order, from glyph positions.

    Some PDFs emit right-to-left words in left-to-right visual order, which
    reverses Arabic phrases. Only lines containing Arabic are rebuilt; all
    other lines, and any input whose positions do not align, are unchanged.
    The rule depends only on geometry, so text already in reading order
    stays as it is.
    """
    if len(x_centres) != len(text) or not _ARABIC.search(text):
        return text
    parts: list[str] = []
    start = 0
    for match in [*_LINE_BREAK.finditer(text), None]:
        end = match.start() if match is not None else len(text)
        line = text[start:end]
        parts.append(
            _reading_order_line(line, x_centres[start:end]) if _ARABIC.search(line) else line
        )
        if match is not None:
            parts.append(match.group())
            start = match.end()
    return "".join(parts)


def _reading_order_line(line: str, x_centres: Sequence[float]) -> str:
    if not all(math.isfinite(x) for x in x_centres):
        return line
    tokens: list[tuple[str, float, bool]] = []
    for match in _WORD.finditer(line):
        word = match.group()
        xs = x_centres[match.start() : match.end()]
        right_to_left = _ARABIC.search(word) is not None
        if right_to_left:
            # An Arabic word reads right to left on the page.
            word = "".join(
                character for character, _ in sorted(zip(word, xs), key=lambda item: -item[1])
            )
        tokens.append((word, sum(xs) / len(xs), right_to_left))
    if not tokens:
        return line
    # A line whose rightmost word is Arabic is a right-to-left line.
    line_right_to_left = max(tokens, key=lambda token: token[1])[2]
    ordered = sorted(tokens, key=lambda token: -token[1] if line_right_to_left else token[1])
    runs: list[list[tuple[str, float, bool]]] = []
    for token in ordered:
        if runs and runs[-1][0][2] == token[2]:
            runs[-1].append(token)
        else:
            runs.append([token])
    words: list[str] = []
    for run in runs:
        # Inside a run, Arabic reads right to left and other text left to right.
        keep = run[0][2] == line_right_to_left
        words.extend(token[0] for token in (run if keep else reversed(run)))
    leading = line[: len(line) - len(line.lstrip())]
    trailing = line[len(line.rstrip()) :]
    return f"{leading}{' '.join(words)}{trailing}"


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
        if _ARABIC.search(text) and text_page.count_chars() == len(text):
            boxes = (text_page.get_charbox(index) for index in range(len(text)))
            text = reading_order_text(text, [(box[0] + box[2]) / 2 for box in boxes])
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
