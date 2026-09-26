"""Read-only browser projections for document readiness."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.document_models import DocumentORM, DocumentVersionORM
from aaos.persistence.rag_models import RagDocumentIndexORM, RagIndexGenerationORM
from aaos.web.contracts import (
    DocumentExtractionStatusView,
    DocumentIndexCoverageView,
    DocumentIndexReadinessView,
    DocumentIngestionStatusView,
    DocumentSummary,
)
from aaos.web.errors import Phase7ApiError


def _to_utc(value: datetime) -> datetime:
    """Normalize a persisted aware timestamp to canonical UTC."""

    if value.tzinfo is None:
        raise ValueError("Persisted document timestamp must be timezone-aware.")
    return value.astimezone(timezone.utc)


class DocumentReadService:
    """Read-only Phase 7 projection service for document readiness metadata."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_documents(self, course_id: int | None = None) -> tuple[DocumentSummary, ...]:
        """Return active document projections in deterministic title/id order."""

        if course_id is not None:
            self._require_active_course(course_id)

        statement = (
            select(DocumentORM, RemoteFileORM, CourseORM)
            .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
            .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
            .where(
                RemoteFileORM.is_active.is_(True),
                CourseORM.is_active.is_(True),
            )
            .order_by(func.lower(RemoteFileORM.title), RemoteFileORM.id, DocumentORM.id)
        )
        if course_id is not None:
            statement = statement.where(RemoteFileORM.course_id == course_id)

        with self._sessions() as session:
            rows = session.execute(statement).tuples().all()
            return self._project_rows(session, rows)

    def get_document(self, document_id: int) -> DocumentSummary:
        """Return one active document or a stable non-leaking not-found error."""

        statement = (
            select(DocumentORM, RemoteFileORM, CourseORM)
            .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
            .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
            .where(
                DocumentORM.id == document_id,
                RemoteFileORM.is_active.is_(True),
                CourseORM.is_active.is_(True),
            )
        )

        with self._sessions() as session:
            row = session.execute(statement).tuples().first()
            if row is None:
                raise self._document_not_found()
            return self._project_rows(session, [row])[0]

    def _require_active_course(self, course_id: int) -> None:
        with self._sessions() as session:
            exists = session.scalar(
                select(CourseORM.id).where(
                    CourseORM.id == course_id,
                    CourseORM.is_active.is_(True),
                )
            )
        if exists is None:
            raise Phase7ApiError(
                404,
                "COURSE_NOT_FOUND",
                "The requested course is not available.",
            )

    @staticmethod
    def _document_not_found() -> Phase7ApiError:
        return Phase7ApiError(
            404,
            "DOCUMENT_NOT_FOUND",
            "The requested document is not available.",
        )

    def _project_rows(
        self,
        session: Session,
        rows: Sequence[tuple[DocumentORM, RemoteFileORM, CourseORM]],
    ) -> tuple[DocumentSummary, ...]:
        document_ids = [document.id for document, _, _ in rows]
        versions_by_document: dict[int, DocumentVersionORM] = {}
        if document_ids:
            version_rows = session.scalars(
                select(DocumentVersionORM)
                .where(DocumentVersionORM.document_id.in_(document_ids))
                .order_by(
                    DocumentVersionORM.document_id,
                    DocumentVersionORM.version_number.desc(),
                    DocumentVersionORM.id.desc(),
                )
            ).all()
            for version in version_rows:
                versions_by_document.setdefault(version.document_id, version)

        active_generation_id = session.scalar(
            select(RagIndexGenerationORM.id).where(RagIndexGenerationORM.status == "ACTIVE")
        )
        indexes_by_version: dict[int, RagDocumentIndexORM] = {}
        version_ids = [version.id for version in versions_by_document.values()]
        if active_generation_id is not None and version_ids:
            indexes = session.scalars(
                select(RagDocumentIndexORM).where(
                    RagDocumentIndexORM.generation_id == active_generation_id,
                    RagDocumentIndexORM.document_version_id.in_(version_ids),
                )
            ).all()
            indexes_by_version = {index.document_version_id: index for index in indexes}

        return tuple(
            self._project_row(
                document,
                remote_file,
                versions_by_document.get(document.id),
                indexes_by_version,
            )
            for document, remote_file, _course in rows
        )

    @staticmethod
    def _project_row(
        document: DocumentORM,
        remote_file: RemoteFileORM,
        version: DocumentVersionORM | None,
        indexes_by_version: dict[int, RagDocumentIndexORM],
    ) -> DocumentSummary:
        try:
            ingestion_status = DocumentIngestionStatusView(document.ingestion_status)
            extraction_status = (
                DocumentExtractionStatusView(version.extraction_status)
                if version is not None
                else None
            )
        except ValueError as error:
            raise Phase7ApiError(
                500,
                "INTERNAL_ERROR",
                "AAOS could not complete the request.",
            ) from error

        readiness: DocumentIndexReadinessView | None = None
        coverage: DocumentIndexCoverageView | None = None
        if version is not None:
            index = indexes_by_version.get(version.id)
            if index is not None:
                try:
                    readiness = DocumentIndexReadinessView(index.status)
                    persisted_coverage = DocumentIndexCoverageView(index.coverage_status)
                except ValueError as error:
                    raise Phase7ApiError(
                        500,
                        "INTERNAL_ERROR",
                        "AAOS could not complete the request.",
                    ) from error

                if readiness is DocumentIndexReadinessView.READY:
                    if persisted_coverage is DocumentIndexCoverageView.NONE:
                        raise Phase7ApiError(
                            500,
                            "INTERNAL_ERROR",
                            "AAOS could not complete the request.",
                        )
                    coverage = persisted_coverage
                elif readiness is DocumentIndexReadinessView.NOT_SEARCHABLE:
                    coverage = DocumentIndexCoverageView.NONE

        return DocumentSummary(
            id=document.id,
            course_id=remote_file.course_id,
            title=remote_file.title,
            ingestion_status=ingestion_status,
            extraction_status=extraction_status,
            page_count=version.page_count if version is not None else None,
            index_readiness=readiness,
            index_coverage=coverage,
            updated_at=_to_utc(document.updated_at),
        )
