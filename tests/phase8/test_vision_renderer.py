from __future__ import annotations

from hashlib import sha256

import pytest

from aaos.phase8.vision_contracts import RenderedPage, VisualPageCandidate


def _candidate() -> VisualPageCandidate:
    from aaos.phase8.vision_contracts import TrustedDocumentVersionBinding
    from aaos.phase8.vision_selector import bind_visual_page_candidate

    return bind_visual_page_candidate(
        VisualPageCandidate(0, 2, "SPARSE_TEXT", 128, "v1"),
        document_version=TrustedDocumentVersionBinding(7),
    )


class FakeRenderer:
    def __init__(self, payload: bytes = b"png-bytes") -> None:
        self.payload = payload
        self.calls: list[tuple[int, int, int]] = []

    def render(self, document_version_id: int, page_number: int, *, max_bytes: int) -> RenderedPage:
        self.calls.append((document_version_id, page_number, max_bytes))
        return RenderedPage(
            document_version_id=document_version_id,
            page_number=page_number,
            media_type="image/png",
            width_px=1200,
            height_px=900,
            content_sha256=sha256(self.payload).hexdigest(),
            content_bytes=self.payload,
        )


def test_render_page_validates_identity_and_passes_bound() -> None:
    from aaos.phase8.vision_renderer import render_page

    renderer = FakeRenderer()
    rendered = render_page(_candidate(), renderer=renderer)

    assert rendered.document_version_id == 7
    assert rendered.page_number == 2
    assert renderer.calls == [(7, 2, 128)]


def test_rendered_page_source_hash_and_byte_bound_are_strict() -> None:
    from aaos.phase8.vision_contracts import RenderedPage

    with pytest.raises(ValueError, match="positive"):
        RenderedPage(0, 2, "image/png", 1200, 900, sha256(b"png-bytes").hexdigest(), b"png-bytes")
    with pytest.raises(ValueError, match="positive"):
        RenderedPage(-1, 2, "image/png", 1200, 900, sha256(b"png-bytes").hexdigest(), b"png-bytes")
    with pytest.raises(ValueError, match="hash"):
        RenderedPage(7, 2, "image/png", 1200, 900, "0" * 64, b"png-bytes")

    with pytest.raises(ValueError, match="bytes"):
        RenderedPage(7, 2, "image/png", 1200, 900, sha256(b"x").hexdigest(), b"x" * 9_000_000)


def test_renderer_rejects_malformed_output() -> None:
    from typing import cast

    from aaos.phase8.vision_contracts import PageRenderer
    from aaos.phase8.vision_renderer import PageRenderError, render_page

    class BadRenderer:
        def render(self, document_version_id: int, page_number: int, *, max_bytes: int) -> object:
            return object()

    with pytest.raises(PageRenderError, match="invalid"):
        render_page(_candidate(), renderer=cast(PageRenderer, BadRenderer()))
