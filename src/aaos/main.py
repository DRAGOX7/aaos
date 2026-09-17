"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.web.contracts import AssignmentSummary, CourseSummary, DocumentSummary
from aaos.web.errors import install_error_boundary
from aaos.web.routes import (
    AcademicReader,
    CourseReader,
    DocumentReader,
    create_api_v1_router,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


class HealthResponse(BaseModel):
    """Stable response contract for process-level health checks."""

    status: Literal["ok"]
    service: str
    version: str


class _LazyDefaultCourseReader:
    """Create default academic persistence only when courses are requested."""

    def __init__(self) -> None:
        self._delegate: AcademicReader | None = None
        self._document_delegate: DocumentReader | None = None
        self._engine: Engine | None = None

    def _ensure_delegate(self) -> AcademicReader:
        if self._delegate is None:
            from aaos.persistence.database import (
                create_database_engine,
                create_session_factory,
            )
            from aaos.web.academic import AcademicReadService
            from aaos.web.documents import DocumentReadService

            engine = create_database_engine()
            sessions = create_session_factory(engine)

            self._engine = engine
            self._delegate = AcademicReadService(sessions)
            self._document_delegate = DocumentReadService(sessions)

        return self._delegate

    def _ensure_document_delegate(self) -> DocumentReader:
        self._ensure_delegate()
        assert self._document_delegate is not None
        return self._document_delegate

    def list_courses(self) -> tuple[CourseSummary, ...]:
        return self._ensure_delegate().list_courses()

    def get_course(self, course_id: int) -> CourseSummary:
        return self._ensure_delegate().get_course(course_id)

    def list_assignments(
        self,
        course_id: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        return self._ensure_delegate().list_assignments(course_id)

    def list_documents(
        self,
        course_id: int | None = None,
    ) -> tuple[DocumentSummary, ...]:
        return self._ensure_document_delegate().list_documents(course_id)

    def get_document(self, document_id: int) -> DocumentSummary:
        return self._ensure_document_delegate().get_document(document_id)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


def create_app(
    settings: AppSettings = DEFAULT_SETTINGS,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create the AAOS API using explicit, non-secret configuration."""

    application = FastAPI(
        title=settings.application_name,
        version=settings.application_version,
    )

    install_error_boundary(application)

    course_reader: CourseReader
    academic_reader: AcademicReader
    document_reader: DocumentReader

    if session_factory is None:
        lazy_course_reader = _LazyDefaultCourseReader()
        course_reader = lazy_course_reader
        academic_reader = lazy_course_reader
        document_reader = lazy_course_reader

        application.router.add_event_handler(
            "shutdown",
            lazy_course_reader.dispose,
        )
    else:
        from aaos.web.academic import AcademicReadService
        from aaos.web.documents import DocumentReadService

        academic_reader = AcademicReadService(session_factory)
        course_reader = academic_reader
        document_reader = DocumentReadService(session_factory)

    application.include_router(
        create_api_v1_router(
            course_reader,
            academic_reader=academic_reader,
            document_reader=document_reader,
        )
    )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Report that the AAOS API process is alive."""

        return HealthResponse(
            status="ok",
            service=settings.application_name,
            version=settings.application_version,
        )

    return application


app = create_app()
