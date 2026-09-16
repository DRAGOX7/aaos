"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from fastapi import APIRouter
from sqlalchemy.orm import Session, sessionmaker

from aaos.web.academic import AcademicReadService
from aaos.web.contracts import CourseListResponse


def create_api_v1_router(
    session_factory: sessionmaker[Session],
) -> APIRouter:
    """Create the versioned Phase 7 browser API router."""

    router = APIRouter(prefix="/api/v1")
    academic = AcademicReadService(session_factory)

    @router.get("/courses", response_model=CourseListResponse)
    def list_courses() -> CourseListResponse:
        return CourseListResponse(
            courses=academic.list_courses(),
        )

    return router
