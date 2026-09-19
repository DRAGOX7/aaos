"""Bounded rendering adapter boundary for selected visual pages."""

from __future__ import annotations

from .vision_contracts import PageRenderer, RenderedPage, VisualPageCandidate


class PageRenderError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " ").lower())


def render_page(candidate: VisualPageCandidate, *, renderer: PageRenderer) -> RenderedPage:
    if not isinstance(candidate, VisualPageCandidate):
        raise TypeError("candidate must be a VisualPageCandidate")
    try:
        rendered = renderer.render(
            candidate.document_version_id,
            candidate.page_number,
            max_bytes=candidate.estimated_bytes,
        )
    except Exception as exc:
        raise PageRenderError("RENDER_FAILED") from exc
    if not isinstance(rendered, RenderedPage):
        raise PageRenderError("INVALID_RENDERED_PAGE")
    if (
        rendered.document_version_id != candidate.document_version_id
        or rendered.page_number != candidate.page_number
        or len(rendered.content_bytes) > candidate.estimated_bytes
    ):
        raise PageRenderError("RENDERED_PAGE_OUT_OF_BOUNDS")
    return rendered


__all__ = ["PageRenderError", "render_page"]
