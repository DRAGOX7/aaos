"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from fastapi import APIRouter, Path, Query

from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalListResponse,
    ApprovalSummary,
    AssignmentListResponse,
    AssignmentSummary,
    CourseListResponse,
    CourseSummary,
    DocumentListResponse,
    DocumentSummary,
    RagAnswerResponse,
    RagQueryRequest,
    TaskDetail,
    TaskListResponse,
    TaskSummary,
)
from aaos.web.errors import Phase7ApiError

if TYPE_CHECKING:
    from aaos.web.rag import AcademicRagReader


class TaskReader(Protocol):
    """Read-only durable task and approval projections."""

    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]: ...

    def get_task(self, task_id: str) -> TaskDetail: ...

    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]: ...

    def get_approval(self, approval_id: str) -> ApprovalDetail: ...


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
    task_reader: TaskReader | None = None,
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
            from aaos.rag.contracts import RagOperationalError

            try:
                return await rag_reader.ask(request)
            except RagOperationalError as error:
                raise Phase7ApiError(
                    503,
                    "RAG_UNAVAILABLE",
                    "AAOS could not answer from the local knowledge base.",
                ) from error

    if task_reader is not None:

        @router.get("/tasks", response_model=TaskListResponse)
        def list_tasks(limit: int = Query(default=100, ge=1, le=100)) -> TaskListResponse:
            return TaskListResponse(tasks=task_reader.list_tasks(limit=limit))

        @router.get("/tasks/{task_id}", response_model=TaskDetail)
        def get_task(task_id: str) -> TaskDetail:
            return task_reader.get_task(task_id)

        @router.get("/approvals", response_model=ApprovalListResponse)
        def list_approvals(limit: int = Query(default=100, ge=1, le=100)) -> ApprovalListResponse:
            return ApprovalListResponse(approvals=task_reader.list_approvals(limit=limit))

        @router.get("/approvals/{approval_id}", response_model=ApprovalDetail)
        def get_approval(approval_id: str) -> ApprovalDetail:
            return task_reader.get_approval(approval_id)

    return router
