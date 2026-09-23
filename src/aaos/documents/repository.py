"""Transactional persistence boundary for Phase 3 document ingestion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aaos.documents.contracts import (
    BlobInstall,
    DocumentIngestionFailure,
    ExtractionStatus,
    IngestionErrorCode,
    IngestionMode,
    IngestionStatus,
    ParsedDocx,
    ParsedPdf,
    RemoteFileSelection,
)
from aaos.persistence.academic_models import RemoteFileORM
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentStructureUnitORM,
    DocumentVersionORM,
)

_PDF_MEDIA_TYPE = "application/pdf"
_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_PAGE_PROVENANCE_MODE = "PAGE"
_STRUCTURE_PROVENANCE_MODE = "STRUCTURE"


@dataclass(frozen=True, slots=True)
class SelectionPlan:
    """Deterministic selected, fetch, and skip partitions for one operation."""

    selected: tuple[RemoteFileSelection, ...]
    fetch: tuple[RemoteFileSelection, ...]
    skipped: tuple[RemoteFileSelection, ...]


@dataclass(frozen=True, slots=True)
class PersistedVersion:
    """Safe identity of a committed or previously existing document version."""

    document_id: int
    version_number: int
    content_blob_id: int
    content_blob_created: bool


class DocumentRepository:
    """Select RemoteFiles and persist one document result per transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def plan_selection(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None,
    ) -> SelectionPlan:
        if remote_file_id is not None and remote_file_id <= 0:
            raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

        session = self._session_factory()
        try:
            statement = (
                select(RemoteFileORM)
                .where(
                    RemoteFileORM.source_system == source_system,
                    RemoteFileORM.is_active.is_(True),
                )
                .order_by(RemoteFileORM.id)
            )
            if remote_file_id is not None:
                statement = statement.where(RemoteFileORM.id == remote_file_id)
            rows = tuple(session.scalars(statement))
            if remote_file_id is not None and not rows:
                raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

            selected = tuple(self._to_selection(row) for row in rows)
            if mode is IngestionMode.REFRESH:
                return SelectionPlan(selected=selected, fetch=selected, skipped=())

            selected_ids = tuple(item.remote_file_id for item in selected)
            ready_ids: set[int] = set()
            if selected_ids:
                ready_ids = set(
                    session.scalars(
                        select(DocumentORM.remote_file_id)
                        .join(
                            DocumentVersionORM,
                            DocumentVersionORM.document_id == DocumentORM.id,
                        )
                        .where(DocumentORM.remote_file_id.in_(selected_ids))
                        .distinct()
                    )
                )
            fetch = tuple(item for item in selected if item.remote_file_id not in ready_ids)
            skipped = tuple(item for item in selected if item.remote_file_id in ready_ids)
            return SelectionPlan(selected=selected, fetch=fetch, skipped=skipped)
        except DocumentIngestionFailure:
            raise
        except SQLAlchemyError:
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED) from None
        finally:
            session.close()

    def existing_version_for_sha256(
        self,
        *,
        remote_file_id: int,
        sha256: str,
    ) -> int | None:
        session = self._session_factory()
        try:
            return session.scalar(
                select(DocumentVersionORM.version_number)
                .join(DocumentORM, DocumentORM.id == DocumentVersionORM.document_id)
                .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
                .where(
                    DocumentORM.remote_file_id == remote_file_id,
                    ContentBlobORM.sha256 == sha256,
                )
            )
        except SQLAlchemyError:
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED) from None
        finally:
            session.close()

    def persist_success(
        self,
        *,
        selection: RemoteFileSelection,
        blob: BlobInstall,
        source_filename: str,
        parsed_pdf: ParsedPdf,
        attempted_at: datetime,
    ) -> PersistedVersion:
        session = self._session_factory()
        try:
            with session.begin():
                document = self._get_or_create_document(
                    session,
                    remote_file_id=selection.remote_file_id,
                    attempted_at=attempted_at,
                )
                existing = self._existing_version(
                    session,
                    document_id=document.id,
                    sha256=blob.sha256,
                    media_type=_PDF_MEDIA_TYPE,
                    provenance_mode=_PAGE_PROVENANCE_MODE,
                )
                if existing is not None:
                    self._mark_ready(document, attempted_at)
                    result = PersistedVersion(
                        document_id=document.id,
                        version_number=existing.version_number,
                        content_blob_id=existing.content_blob_id,
                        content_blob_created=False,
                    )
                else:
                    content_blob, content_blob_created = self._get_or_create_blob(
                        session,
                        blob=blob,
                        media_type=_PDF_MEDIA_TYPE,
                        attempted_at=attempted_at,
                    )
                    version_number = self._next_version_number(session, document.id)
                    version = DocumentVersionORM(
                        document_id=document.id,
                        version_number=version_number,
                        content_blob_id=content_blob.id,
                        source_filename=source_filename,
                        ingested_at=attempted_at,
                        extraction_status=parsed_pdf.extraction_status.value,
                        provenance_mode=_PAGE_PROVENANCE_MODE,
                        page_count=parsed_pdf.page_count,
                    )
                    session.add(version)
                    session.flush()
                    session.add_all(
                        DocumentPageORM(
                            document_version_id=version.id,
                            page_number=page.page_number,
                            text=page.text,
                            text_source=page.text_source.value,
                            width_points=page.width_points,
                            height_points=page.height_points,
                            created_at=attempted_at,
                        )
                        for page in parsed_pdf.pages
                    )
                    self._mark_ready(document, attempted_at)
                    session.flush()
                    result = PersistedVersion(
                        document_id=document.id,
                        version_number=version.version_number,
                        content_blob_id=content_blob.id,
                        content_blob_created=content_blob_created,
                    )
            return result
        except SQLAlchemyError:
            session.rollback()
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED) from None
        finally:
            session.close()


    def persist_structural_success(
        self,
        *,
        selection: RemoteFileSelection,
        blob: BlobInstall,
        source_filename: str,
        parsed_docx: ParsedDocx,
        attempted_at: datetime,
    ) -> PersistedVersion:
        session = self._session_factory()
        try:
            with session.begin():
                document = self._get_or_create_document(
                    session,
                    remote_file_id=selection.remote_file_id,
                    attempted_at=attempted_at,
                )
                existing = self._existing_version(
                    session,
                    document_id=document.id,
                    sha256=blob.sha256,
                    media_type=_DOCX_MEDIA_TYPE,
                    provenance_mode=_STRUCTURE_PROVENANCE_MODE,
                )
                if existing is not None:
                    self._mark_ready(document, attempted_at)
                    result = PersistedVersion(
                        document_id=document.id,
                        version_number=existing.version_number,
                        content_blob_id=existing.content_blob_id,
                        content_blob_created=False,
                    )
                else:
                    content_blob, content_blob_created = self._get_or_create_blob(
                        session,
                        blob=blob,
                        media_type=_DOCX_MEDIA_TYPE,
                        attempted_at=attempted_at,
                    )
                    version_number = self._next_version_number(session, document.id)
                    version = DocumentVersionORM(
                        document_id=document.id,
                        version_number=version_number,
                        content_blob_id=content_blob.id,
                        source_filename=source_filename,
                        ingested_at=attempted_at,
                        extraction_status=ExtractionStatus.SUCCESS.value,
                        provenance_mode=_STRUCTURE_PROVENANCE_MODE,
                        page_count=None,
                    )
                    session.add(version)
                    session.flush()
                    session.add_all(
                        DocumentStructureUnitORM(
                            document_version_id=version.id,
                            ordinal=unit.ordinal,
                            kind=unit.kind.value,
                            text=unit.text,
                            heading_level=unit.heading_level,
                            table_index=unit.table_index,
                            row_index=unit.row_index,
                            column_index=unit.column_index,
                            created_at=attempted_at,
                        )
                        for unit in parsed_docx.units
                    )
                    self._mark_ready(document, attempted_at)
                    session.flush()
                    result = PersistedVersion(
                        document_id=document.id,
                        version_number=version.version_number,
                        content_blob_id=content_blob.id,
                        content_blob_created=content_blob_created,
                    )
            return result
        except SQLAlchemyError:
            session.rollback()
            raise DocumentIngestionFailure(
                IngestionErrorCode.PERSISTENCE_FAILED
            ) from None
        finally:
            session.close()

    def record_failure(
        self,
        *,
        selection: RemoteFileSelection,
        code: IngestionErrorCode,
        attempted_at: datetime,
    ) -> None:
        session = self._session_factory()
        try:
            with session.begin():
                document = self._get_or_create_document(
                    session,
                    remote_file_id=selection.remote_file_id,
                    attempted_at=attempted_at,
                )
                has_version = (
                    session.scalar(
                        select(DocumentVersionORM.id)
                        .where(DocumentVersionORM.document_id == document.id)
                        .limit(1)
                    )
                    is not None
                )
                document.ingestion_status = (
                    IngestionStatus.READY.value if has_version else IngestionStatus.FAILED.value
                )
                document.last_attempted_at = attempted_at
                document.last_error_code = code.value
                document.updated_at = attempted_at
                session.flush()
        except SQLAlchemyError:
            session.rollback()
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED) from None
        finally:
            session.close()

    def blob_is_referenced(self, *, sha256: str) -> bool:
        session = self._session_factory()
        try:
            return (
                session.scalar(
                    select(DocumentVersionORM.id)
                    .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
                    .where(ContentBlobORM.sha256 == sha256)
                    .limit(1)
                )
                is not None
            )
        except SQLAlchemyError:
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED) from None
        finally:
            session.close()

    @staticmethod
    def _to_selection(row: RemoteFileORM) -> RemoteFileSelection:
        return RemoteFileSelection(
            remote_file_id=row.id,
            course_id=row.course_id,
            source_system=row.source_system,
            source_id=row.source_id,
            title=row.title,
        )

    @staticmethod
    def _get_or_create_document(
        session: Session,
        *,
        remote_file_id: int,
        attempted_at: datetime,
    ) -> DocumentORM:
        document = session.scalar(
            select(DocumentORM).where(DocumentORM.remote_file_id == remote_file_id)
        )
        if document is None:
            document = DocumentORM(
                remote_file_id=remote_file_id,
                ingestion_status=IngestionStatus.NEVER_ATTEMPTED.value,
                last_attempted_at=None,
                last_error_code=None,
                created_at=attempted_at,
                updated_at=attempted_at,
            )
            session.add(document)
            session.flush()
        return document

    @staticmethod
    def _existing_version(
        session: Session,
        *,
        document_id: int,
        sha256: str,
        media_type: str,
        provenance_mode: str,
    ) -> DocumentVersionORM | None:
        return session.scalar(
            select(DocumentVersionORM)
            .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
            .where(
                DocumentVersionORM.document_id == document_id,
                ContentBlobORM.sha256 == sha256,
                ContentBlobORM.media_type == media_type,
                DocumentVersionORM.provenance_mode == provenance_mode,
            )
        )

    @staticmethod
    def _get_or_create_blob(
        session: Session,
        *,
        blob: BlobInstall,
        media_type: str,
        attempted_at: datetime,
    ) -> tuple[ContentBlobORM, bool]:
        content_blob = session.scalar(
            select(ContentBlobORM).where(ContentBlobORM.sha256 == blob.sha256)
        )
        if content_blob is not None:
            if (
                content_blob.size_bytes != blob.size_bytes
                or content_blob.media_type != media_type
                or content_blob.storage_key != blob.storage_key
            ):
                raise DocumentIngestionFailure(
                    IngestionErrorCode.PERSISTENCE_FAILED
                )
            return content_blob, False
        content_blob = ContentBlobORM(
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            media_type=media_type,
            storage_key=blob.storage_key,
            created_at=attempted_at,
        )
        session.add(content_blob)
        session.flush()
        return content_blob, True

    @staticmethod
    def _next_version_number(session: Session, document_id: int) -> int:
        current = session.scalar(
            select(func.max(DocumentVersionORM.version_number)).where(
                DocumentVersionORM.document_id == document_id
            )
        )
        return (current or 0) + 1

    @staticmethod
    def _mark_ready(document: DocumentORM, attempted_at: datetime) -> None:
        document.ingestion_status = IngestionStatus.READY.value
        document.last_attempted_at = attempted_at
        document.last_error_code = None
        document.updated_at = attempted_at
