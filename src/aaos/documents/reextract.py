"""Re-extract one stored PDF version's page text with the current extractor.

Page text is normally immutable once ingested. This is a deliberate, narrow
maintenance exception for extractor fixes (such as Arabic reading order): it
reads only the locally stored, hash-verified file, refuses any change to the
page structure, and rewrites page text in one transaction. Rebuild the RAG
index afterwards so chunks are derived from the corrected pages.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.documents.contracts import DocumentIngestionFailure
from aaos.documents.pdf import PdfProcessor
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentPageORM,
    DocumentVersionORM,
)

_PDF_MEDIA_TYPE = "application/pdf"
_PAGE_PROVENANCE_MODE = "PAGE"


class ReextractError(RuntimeError):
    """A fixed, safe re-extraction failure code."""

    def __init__(self, code: str) -> None:
        super().__init__("document re-extraction failed")
        self.code = code


@dataclass(frozen=True, slots=True)
class ReextractResult:
    document_version_id: int
    page_count: int
    changed_pages: tuple[int, ...]


def reextract_pdf_version(
    session_factory: Callable[[], Session],
    content_store_root: Path,
    document_version_id: int,
    *,
    processor: PdfProcessor | None = None,
) -> ReextractResult:
    """Rewrite the page text of one PDF version from its stored file."""
    session = session_factory()
    try:
        with session.begin():
            row = session.execute(
                select(DocumentVersionORM, ContentBlobORM)
                .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
                .where(DocumentVersionORM.id == document_version_id)
            ).one_or_none()
            if row is None:
                raise ReextractError("VERSION_NOT_FOUND")
            stored_version, stored_blob = row
            if (
                stored_version.provenance_mode != _PAGE_PROVENANCE_MODE
                or stored_blob.media_type != _PDF_MEDIA_TYPE
            ):
                raise ReextractError("NOT_A_PAGE_PDF")
            body = _read_verified_blob(
                content_store_root,
                stored_blob.storage_key,
                sha256=stored_blob.sha256,
                size_bytes=stored_blob.size_bytes,
            )
            try:
                parsed = (processor or PdfProcessor()).process(body)
            except DocumentIngestionFailure:
                raise ReextractError("EXTRACTION_FAILED") from None
            pages = list(
                session.scalars(
                    select(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == document_version_id)
                    .order_by(DocumentPageORM.page_number)
                )
            )
            if (
                parsed.page_count != stored_version.page_count
                or len(pages) != parsed.page_count
                or [item.page_number for item in pages]
                != [item.page_number for item in parsed.pages]
            ):
                raise ReextractError("PAGE_COUNT_CHANGED")
            if parsed.extraction_status.value != stored_version.extraction_status:
                raise ReextractError("EXTRACTION_STATUS_CHANGED")
            changed: list[int] = []
            for stored_page, fresh_page in zip(pages, parsed.pages, strict=True):
                if stored_page.text != fresh_page.text:
                    stored_page.text = fresh_page.text
                    changed.append(stored_page.page_number)
        return ReextractResult(document_version_id, parsed.page_count, tuple(changed))
    except SQLAlchemyError:
        raise ReextractError("PERSISTENCE_FAILED") from None
    finally:
        session.close()


def _read_verified_blob(root: Path, storage_key: str, *, sha256: str, size_bytes: int) -> bytes:
    """Read a stored blob only from inside the content store and only if it matches."""
    resolved_root = root.resolve()
    path = (resolved_root / storage_key).resolve()
    if not path.is_relative_to(resolved_root) or not path.is_file():
        raise ReextractError("BLOB_INTEGRITY_FAILED")
    try:
        body = path.read_bytes()
    except OSError:
        raise ReextractError("BLOB_INTEGRITY_FAILED") from None
    if len(body) != size_bytes or hashlib.sha256(body).hexdigest() != sha256:
        raise ReextractError("BLOB_INTEGRITY_FAILED")
    return body
