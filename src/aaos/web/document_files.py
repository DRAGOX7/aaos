"""Read the original file of a synced document, for viewing in the browser.

Only a file recorded for a visible document is served, only from inside the
local content store, and only if its bytes still match the recorded hash.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.visibility import item_visible
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.document_models import ContentBlobORM, DocumentORM, DocumentVersionORM
from aaos.web.contracts import CitationEvidenceView
from aaos.web.errors import Phase7ApiError

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXTENSIONS = {PDF: ".pdf", DOCX: ".docx"}


@dataclass(frozen=True, slots=True)
class DocumentFile:
    body: bytes
    media_type: str
    filename: str


def _not_available() -> Phase7ApiError:
    return Phase7ApiError(404, "DOCUMENT_FILE_NOT_FOUND", "The document file is not available.")


def _safe_filename(title: str, media_type: str) -> str:
    stem = "".join(ch for ch in title if ch.isprintable() and ch not in '\\/:*?"<>|').strip()
    stem = stem or "document"
    extension = _EXTENSIONS[media_type]
    return stem if stem.lower().endswith(extension) else stem + extension


class DocumentFileService:
    def __init__(self, sessions: sessionmaker[Session], content_root: Path) -> None:
        self._sessions = sessions
        self._content_root = content_root

    def open_document_file(self, document_id: int) -> DocumentFile:
        return self._open_file(document_id=document_id)

    def open_document_version_file(self, version_id: int) -> DocumentFile:
        return self._open_file(version_id=version_id)

    def _open_file(
        self, *, document_id: int | None = None, version_id: int | None = None
    ) -> DocumentFile:
        statement = (
            select(RemoteFileORM.title, ContentBlobORM)
            .join(DocumentORM, DocumentORM.remote_file_id == RemoteFileORM.id)
            .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
            .join(DocumentVersionORM, DocumentVersionORM.document_id == DocumentORM.id)
            .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
            .where(
                DocumentORM.id == document_id
                if version_id is None
                else DocumentVersionORM.id == version_id,
                item_visible(RemoteFileORM, include_past=True),
            )
            .order_by(DocumentVersionORM.version_number.desc())
            .limit(1)
        )
        with self._sessions() as session:
            row = session.execute(statement).tuples().first()
        if row is None:
            raise _not_available()
        title, blob = row
        if blob.media_type not in _EXTENSIONS:
            raise _not_available()
        root = self._content_root.resolve()
        path = (root / blob.storage_key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise _not_available()
        try:
            body = path.read_bytes()
        except OSError:
            raise _not_available() from None
        if len(body) != blob.size_bytes or hashlib.sha256(body).hexdigest() != blob.sha256:
            raise _not_available()
        return DocumentFile(body, blob.media_type, _safe_filename(title, blob.media_type))

    def read_evidence(self, version_id: int, start: int, end: int) -> CitationEvidenceView:
        from aaos.persistence.document_models import DocumentPageORM, DocumentStructureUnitORM

        statement = (
            select(RemoteFileORM.title, CourseORM.title, DocumentVersionORM.provenance_mode)
            .join(DocumentORM, DocumentORM.remote_file_id == RemoteFileORM.id)
            .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
            .join(DocumentVersionORM, DocumentVersionORM.document_id == DocumentORM.id)
            .where(
                DocumentVersionORM.id == version_id, item_visible(RemoteFileORM, include_past=True)
            )
        )
        if start < 1 or end < start:
            raise Phase7ApiError(422, "INVALID_REQUEST", "The source location is invalid.")
        excerpt_end = min(end, start + 19)
        with self._sessions() as session:
            row = session.execute(statement).tuples().first()
            if row is None:
                raise _not_available()
            title, course, mode = row
            if mode == "PAGE":
                texts = session.scalars(
                    select(DocumentPageORM.text)
                    .where(
                        DocumentPageORM.document_version_id == version_id,
                        DocumentPageORM.page_number.between(start, excerpt_end),
                    )
                    .order_by(DocumentPageORM.page_number)
                ).all()
            else:
                texts = session.scalars(
                    select(DocumentStructureUnitORM.text)
                    .where(
                        DocumentStructureUnitORM.document_version_id == version_id,
                        DocumentStructureUnitORM.ordinal.between(start, excerpt_end),
                    )
                    .order_by(DocumentStructureUnitORM.ordinal)
                ).all()
        # Bound the browser payload; never paraphrase or synthesize source text.
        remaining = 16000
        excerpts: list[str] = []
        truncated = excerpt_end < end
        for text in texts:
            excerpt = text[:remaining]
            truncated |= len(excerpt) < len(text)
            if excerpt:
                excerpts.append(excerpt)
            remaining -= len(excerpt)
        return CitationEvidenceView(
            document_version_id=version_id,
            document_title=title,
            course_title=course,
            location_kind="PAGE" if mode == "PAGE" else "STRUCTURE",
            start=start,
            end=end,
            excerpts=tuple(excerpts),
            truncated=truncated,
        )
