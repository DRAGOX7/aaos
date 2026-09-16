"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from typing import Protocol

from fastapi import APIRouter

from aaos.web.contracts import CourseListResponse, CourseSummary


class CourseReader(Protocol):
    """Read-only course projection required by the HTTP boundary."""

    def list_courses(self) -> tuple[CourseSummary, ...]:
        """Return browser-safe active courses."""


def create_api_v1_router(
    course_reader: CourseReader,
) -> APIRouter:
    """Create the versioned Phase 7 browser API router."""

    router = APIRouter(prefix="/api/v1")

    @router.get("/courses", response_model=CourseListResponse)
    def list_courses() -> CourseListResponse:
        return CourseListResponse(
            courses=course_reader.list_courses(),
        )

    return router
