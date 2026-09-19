"""Deterministic visual-page selection without rendering or model calls."""

from __future__ import annotations

from aaos.documents.contracts import ParsedPdf

from .vision_contracts import (
    MAX_RENDER_BYTES,
    VisualPageCandidate,
    VisualReasonCode,
    VisualSelectorPolicy,
)


def select_visual_pages(
    parsed_pdf: ParsedPdf, *, selector_policy: VisualSelectorPolicy
) -> tuple[VisualPageCandidate, ...]:
    if not isinstance(parsed_pdf, ParsedPdf):
        raise TypeError("parsed_pdf must be a ParsedPdf")
    if not isinstance(selector_policy, VisualSelectorPolicy):
        raise TypeError("selector_policy must be a VisualSelectorPolicy")
    if parsed_pdf.page_count <= 0 or len(parsed_pdf.pages) != parsed_pdf.page_count:
        raise ValueError("page count metadata is inconsistent")
    page_numbers = [page.page_number for page in parsed_pdf.pages]
    if len(set(page_numbers)) != len(page_numbers) or any(
        page_number <= 0 or page_number > parsed_pdf.page_count for page_number in page_numbers
    ):
        raise ValueError("page metadata is inconsistent")
    document_version_id = getattr(parsed_pdf, "document_version_id", 0)
    if isinstance(document_version_id, bool) or not isinstance(document_version_id, int):
        raise ValueError("document version metadata is invalid")
    candidates: list[tuple[int, int, VisualPageCandidate]] = []
    for page in parsed_pdf.pages:
        text_length = len(page.text.strip())
        area = page.width_points * page.height_points
        if area <= 0:
            raise ValueError("page geometry is invalid")
        if text_length == 0:
            reason = VisualReasonCode.EMPTY_PAGE.value
        elif text_length <= 240 or text_length / area < 0.001:
            reason = VisualReasonCode.SPARSE_TEXT.value
        else:
            continue
        estimate = min(
            selector_policy.max_render_bytes,
            max(1024, int(area * 4)),
            MAX_RENDER_BYTES,
        )
        candidates.append(
            (
                text_length,
                page.page_number,
                VisualPageCandidate(
                    document_version_id=document_version_id,
                    page_number=page.page_number,
                    reason_code=reason,
                    estimated_bytes=estimate,
                    selector_version=selector_policy.selector_version,
                ),
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in candidates[: selector_policy.max_pages])


__all__ = ["select_visual_pages"]
