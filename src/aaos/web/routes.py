"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from typing import Protocol

from fastapi import APIRouter, Path, Query

from aaos.rag.contracts import RagOperationalError
from aaos.web.contracts import (
    AssignmentListResponse,
    AssignmentSummary,
    CourseListResponse,
    CourseSummary,
    DocumentListResponse,
    DocumentSummary,
    RagAnswerResponse,
    RagQueryRequest,
)
from aaos.web.errors import Phase7ApiError
from aaos.web.rag import AcademicRagReader


class CourseReader(Protocol):
    """Read-only course projection required by the HTTP boundary."""

    def list_courses(self) -> tuple[CourseSummary, ...]:
        """Return browser-safe active courses."""


class AcademicReader(CourseReader, Protocol):
    """Read-only academic projections required by Task 2 routes."""

    def get_course(self, course_id: int) -> CourseSummary:
        """Return one active course."""

    def list_assignments(
        self,
        course_id: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        """Return browser-safe active assignments."""


class DocumentReader(Protocol):
    """Read-only document projection required by Task 3 routes."""

    def list_documents(
        self,
        course_id: int | None = None,
    ) -> tuple[DocumentSummary, ...]:
        """Return browser-safe active documents."""

    def get_document(self, document_id: int) -> DocumentSummary:
        """Return one active document."""


def create_api_v1_router(
    course_reader: CourseReader,
    *,
    academic_reader: AcademicReader | None = None,
    document_reader: DocumentReader | None = None,
    rag_reader: AcademicRagReader | None = None,
) -> APIRouter:
    """Create the versioned Phase 7 browser API router."""

    router = APIRouter(prefix="/api/v1")

    @router.get("/courses", response_model=CourseListResponse)
    def list_courses() -> CourseListResponse:
        return CourseListResponse(
            courses=course_reader.list_courses(),
        )

    if academic_reader is not None:

        @router.get(
            "/courses/{course_id}",
            response_model=CourseSummary,
        )
        def get_course(
            course_id: int = Path(..., gt=0),
        ) -> CourseSummary:
            return academic_reader.get_course(course_id)

        @router.get(
            "/assignments",
            response_model=AssignmentListResponse,
        )
        def list_assignments(
            course_id: int | None = Query(default=None, gt=0),
        ) -> AssignmentListResponse:
            return AssignmentListResponse(
                assignments=academic_reader.list_assignments(course_id),
            )

    if document_reader is not None:

        @router.get(
            "/documents",
            response_model=DocumentListResponse,
        )
        def list_documents(
            course_id: int | None = Query(default=None, gt=0),
        ) -> DocumentListResponse:
            return DocumentListResponse(
                documents=document_reader.list_documents(course_id),
            )

        @router.get(
            "/documents/{document_id}",
            response_model=DocumentSummary,
        )
        def get_document(
            document_id: int = Path(..., gt=0),
        ) -> DocumentSummary:
            return document_reader.get_document(document_id)

    if rag_reader is not None:

        @router.post(
            "/rag/queries",
            response_model=RagAnswerResponse,
        )
        async def ask_rag(request: RagQueryRequest) -> RagAnswerResponse:
            try:
                return await rag_reader.ask(request)
            except RagOperationalError as error:
                raise Phase7ApiError(
                    503,
                    "RAG_UNAVAILABLE",
                    "AAOS could not answer from the local knowledge base.",
                ) from error

    return router
