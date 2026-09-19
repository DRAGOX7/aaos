from __future__ import annotations

import pytest

from aaos.documents.contracts import ExtractedPage, ExtractionStatus, ParsedPdf, TextSource
from aaos.phase8.vision_contracts import VisualSelectorPolicy


def _parsed(*texts: str, page_count: int | None = None) -> ParsedPdf:
    pages = tuple(
        ExtractedPage(index, text, TextSource.EMBEDDED_PDF, 612.0, 792.0)
        for index, text in enumerate(texts, start=1)
    )
    return ParsedPdf(
        page_count=len(pages) if page_count is None else page_count,
        extraction_status=ExtractionStatus.PARTIAL,
        pages=pages,
    )


def _policy(max_pages: int = 2) -> VisualSelectorPolicy:
    return VisualSelectorPolicy(
        max_pages=max_pages, max_render_bytes=100_000, selector_version="v1"
    )


def test_ordinary_dense_text_page_is_not_selected() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("ordinary academic prose " * 500)

    assert select_visual_pages(parsed, selector_policy=_policy()) == ()


def test_sparse_and_empty_pages_are_selected_deterministically_and_budgeted() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("", "short label", "ordinary academic prose " * 500)
    first = select_visual_pages(parsed, selector_policy=_policy(max_pages=2))
    second = select_visual_pages(parsed, selector_policy=_policy(max_pages=2))

    assert first == second
    assert len(first) == 2
    assert tuple(item.page_number for item in first) == (1, 2)
    assert all(item.document_version_id == 0 for item in first)
    assert all(item.estimated_bytes > 0 for item in first)
    assert all(item.selector_version == "v1" for item in first)


def test_malformed_page_metadata_is_rejected_without_out_of_range_candidates() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("", page_count=3)

    with pytest.raises(ValueError, match="page count"):
        select_visual_pages(parsed, selector_policy=_policy())


def test_selector_rejects_invalid_policy() -> None:
    from aaos.phase8.vision_contracts import VisualSelectorPolicy

    with pytest.raises(ValueError):
        VisualSelectorPolicy(max_pages=0, max_render_bytes=100, selector_version="v1")
