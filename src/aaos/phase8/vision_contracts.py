"""Closed, bounded contracts for deterministic Phase 8 visual work."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

MAX_VISUAL_PAGES = 32
MAX_RENDER_BYTES = 8 * 1024 * 1024
MAX_RENDER_WIDTH = 4096
MAX_RENDER_HEIGHT = 4096
MAX_RENDER_PIXELS = 16_777_216
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VisualReasonCode(StrEnum):
    EMPTY_PAGE = "EMPTY_PAGE"
    SPARSE_TEXT = "SPARSE_TEXT"


@dataclass(frozen=True, slots=True)
class TrustedDocumentVersionBinding:
    """Positive document-version identity resolved by trusted persistence code."""

    document_version_id: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.document_version_id, bool)
            or not isinstance(self.document_version_id, int)
            or self.document_version_id <= 0
        ):
            raise ValueError("document_version_id must be positive")


@dataclass(frozen=True, slots=True)
class VisualPageCandidate:
    document_version_id: int
    page_number: int
    reason_code: str
    estimated_bytes: int
    selector_version: str
    _trusted_document_version: TrustedDocumentVersionBinding | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if isinstance(self.document_version_id, bool) or not isinstance(
            self.document_version_id, int
        ):
            raise TypeError("document_version_id must be an integer")
        if self.document_version_id < 0:
            raise ValueError("document_version_id must be non-negative")
        if self._trusted_document_version is None and self.document_version_id != 0:
            raise ValueError("positive document_version_id requires trusted binding")
        if self._trusted_document_version is not None and (
            self.document_version_id != self._trusted_document_version.document_version_id
        ):
            raise ValueError("document_version_id does not match trusted binding")
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int):
            raise TypeError("page_number must be an integer")
        if self.page_number <= 0:
            raise ValueError("page_number must be positive")
        if not isinstance(self.reason_code, str) or not _SAFE_IDENTIFIER.fullmatch(
            self.reason_code
        ):
            raise ValueError("reason_code must be a safe identifier")
        if isinstance(self.estimated_bytes, bool) or not isinstance(self.estimated_bytes, int):
            raise TypeError("estimated_bytes must be an integer")
        if self.estimated_bytes <= 0 or self.estimated_bytes > MAX_RENDER_BYTES:
            raise ValueError("estimated_bytes exceeds the render bound")
        if not isinstance(self.selector_version, str) or not _SAFE_IDENTIFIER.fullmatch(
            self.selector_version
        ):
            raise ValueError("selector_version must be a safe identifier")


@dataclass(frozen=True, slots=True)
class VisualSelectorPolicy:
    max_pages: int
    max_render_bytes: int
    selector_version: str

    def __post_init__(self) -> None:
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int):
            raise TypeError("max_pages must be an integer")
        if self.max_pages <= 0 or self.max_pages > MAX_VISUAL_PAGES:
            raise ValueError("max_pages exceeds the visual page bound")
        if isinstance(self.max_render_bytes, bool) or not isinstance(self.max_render_bytes, int):
            raise TypeError("max_render_bytes must be an integer")
        if self.max_render_bytes <= 0 or self.max_render_bytes > MAX_RENDER_BYTES:
            raise ValueError("max_render_bytes exceeds the render bound")
        if not isinstance(self.selector_version, str) or not _SAFE_IDENTIFIER.fullmatch(
            self.selector_version
        ):
            raise ValueError("selector_version must be a safe identifier")


@dataclass(frozen=True, slots=True)
class RenderedPage:
    document_version_id: int
    page_number: int
    media_type: Literal["image/png", "image/jpeg"]
    width_px: int
    height_px: int
    content_sha256: str
    content_bytes: bytes

    def __post_init__(self) -> None:
        if isinstance(self.document_version_id, bool) or not isinstance(
            self.document_version_id, int
        ):
            raise TypeError("document_version_id must be an integer")
        if self.document_version_id <= 0:
            raise ValueError("document_version_id must be positive")
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int):
            raise TypeError("page_number must be an integer")
        if self.page_number <= 0:
            raise ValueError("page_number must be positive")
        if self.media_type not in {"image/png", "image/jpeg"}:
            raise ValueError("media_type is unsupported")
        for name, value, maximum in (
            ("width_px", self.width_px, MAX_RENDER_WIDTH),
            ("height_px", self.height_px, MAX_RENDER_HEIGHT),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                or value > maximum
            ):
                raise ValueError(f"{name} exceeds the render bound")
        if self.width_px * self.height_px > MAX_RENDER_PIXELS:
            raise ValueError("rendered page exceeds the pixel bound")
        if not isinstance(self.content_bytes, bytes) or not self.content_bytes:
            raise ValueError("content_bytes must contain bytes")
        if len(self.content_bytes) > MAX_RENDER_BYTES:
            raise ValueError("content_bytes exceeds the render byte bound")
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if hashlib.sha256(self.content_bytes).hexdigest() != self.content_sha256:
            raise ValueError("content hash does not match content_bytes")


class PageRenderer(Protocol):
    def render(
        self, document_version_id: int, page_number: int, *, max_bytes: int
    ) -> RenderedPage: ...


__all__ = [
    "MAX_RENDER_BYTES",
    "MAX_RENDER_HEIGHT",
    "MAX_RENDER_PIXELS",
    "MAX_RENDER_WIDTH",
    "MAX_VISUAL_PAGES",
    "PageRenderer",
    "RenderedPage",
    "TrustedDocumentVersionBinding",
    "VisualPageCandidate",
    "VisualReasonCode",
    "VisualSelectorPolicy",
]
