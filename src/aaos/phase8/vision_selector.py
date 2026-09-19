"""Deterministic visual-page selection without rendering or model calls."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import inspect

from aaos.documents.contracts import ParsedPdf
from aaos.persistence.document_models import DocumentVersionORM

from .vision_contracts import (
    MAX_RENDER_BYTES,
    TrustedDocumentVersionBinding,
    VisualPageCandidate,
    VisualReasonCode,
    VisualSelectorPolicy,
)


def trusted_document_version_binding(
    document_version: DocumentVersionORM,
) -> TrustedDocumentVersionBinding:
    """Issue a binding only for a persistent authoritative document-version row."""

    if not isinstance(document_version, DocumentVersionORM):
        raise TypeError("document_version must be a DocumentVersionORM")
    if not inspect(document_version).persistent:
        raise ValueError("document_version must be a persistent ORM row")
    return TrustedDocumentVersionBinding._from_persistence(document_version.id)


def bind_visual_page_candidate(
    candidate: VisualPageCandidate,
    *,
    document_version: TrustedDocumentVersionBinding,
) -> VisualPageCandidate:
    """Bind an unpersisted selector result to a trusted positive version ID."""

    if not isinstance(candidate, VisualPageCandidate):
        raise TypeError("candidate must be a VisualPageCandidate")
    if not isinstance(document_version, TrustedDocumentVersionBinding):
        raise TypeError("document_version must be a TrustedDocumentVersionBinding")
    if candidate.document_version_id != 0:
        raise ValueError("candidate must be unbound before trusted binding")
    return replace(
        candidate,
        document_version_id=document_version.document_version_id,
        _trusted_document_version=document_version,
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
    document_version_id = 0
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


__all__ = [
    "bind_visual_page_candidate",
    "select_visual_pages",
    "trusted_document_version_binding",
]
