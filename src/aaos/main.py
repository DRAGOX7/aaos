"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.web.contracts import CourseSummary
from aaos.web.errors import install_error_boundary
from aaos.web.routes import CourseReader, create_api_v1_router

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
        self._delegate: CourseReader | None = None
        self._engine: Engine | None = None

    def list_courses(self) -> tuple[CourseSummary, ...]:
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

        return self._delegate.list_courses()

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

    if session_factory is None:
        lazy_course_reader = _LazyDefaultCourseReader()
        course_reader = lazy_course_reader

        application.router.add_event_handler(
            "shutdown",
            lazy_course_reader.dispose,
        )
    else:
        from aaos.web.academic import AcademicReadService

        course_reader = AcademicReadService(session_factory)

    application.include_router(
        create_api_v1_router(course_reader)
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
