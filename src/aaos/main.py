"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.web.contracts import AssignmentSummary, CourseSummary
from aaos.web.errors import install_error_boundary
from aaos.web.routes import AcademicReader, CourseReader, create_api_v1_router

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
        self._engine: Engine | None = None

    def _ensure_delegate(self) -> AcademicReader:
        if self._delegate is None:
            from aaos.persistence.database import (
                create_database_engine,
                create_session_factory,
            )
            from aaos.web.academic import AcademicReadService

            engine = create_database_engine()
            sessions = create_session_factory(engine)

            self._engine = engine
            self._delegate = AcademicReadService(sessions)

        return self._delegate

    def list_courses(self) -> tuple[CourseSummary, ...]:
        return self._ensure_delegate().list_courses()

    def get_course(self, course_id: int) -> CourseSummary:
        return self._ensure_delegate().get_course(course_id)

    def list_assignments(
        self,
        course_id: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        return self._ensure_delegate().list_assignments(course_id)

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

    if session_factory is None:
        lazy_course_reader = _LazyDefaultCourseReader()
        course_reader = lazy_course_reader
        academic_reader = lazy_course_reader

        application.router.add_event_handler(
            "shutdown",
            lazy_course_reader.dispose,
        )
    else:
        from aaos.web.academic import AcademicReadService

        academic_reader = AcademicReadService(session_factory)
        course_reader = academic_reader

    application.include_router(
        create_api_v1_router(
            course_reader,
            academic_reader=academic_reader,
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
