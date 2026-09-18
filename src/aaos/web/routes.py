"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from fastapi import APIRouter, Body, Path, Query, Request
from fastapi.responses import StreamingResponse

from aaos.web.activity import (
    ActivityCursor,
    ActivityEvent,
    ActivityHistoryResponse,
    decode_activity_cursor,
)
from aaos.web.activity_stream import iter_activity_sse
from aaos.web.contracts import (
    ApprovalCommandRequest,
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
from aaos.web.health import SystemHealthResponse
from aaos.web.mutation_security import MutationRequestRejected, enforce_mutation_fence

if TYPE_CHECKING:
    from aaos.approval.contracts import ApprovalId, Phase6ErrorCode, TrustedApproverContext
    from aaos.web.principal import LocalApproverContextProvider
    from aaos.web.rag import AcademicRagReader


class TaskReader(Protocol):
    """Read-only durable task and approval projections."""

    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]: ...

    def get_task(self, task_id: str) -> TaskDetail: ...

    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]: ...

    def get_approval(self, approval_id: str) -> ApprovalDetail: ...


class TaskApprovalCommands(Protocol):
    """Trusted approval decisions delegated to the Phase 6 service."""

    def approve(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail: ...

    def reject(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail: ...


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


class ActivityReader(Protocol):
    """Read-only Activity history and tail projection."""

    def list_history(
        self, *, limit: int, before: ActivityCursor | None = None
    ) -> ActivityHistoryResponse:
        """Return newest-first browser-safe Activity history."""

    def tail(self, *, after: ActivityCursor | None, limit: int) -> tuple[ActivityEvent, ...]:
        """Return strictly-after browser-safe Activity rows."""

    def tail_with_cursors(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]:
        """Return strictly-after events with opaque replay cursors."""
        ...

    def current_high_watermark(self) -> ActivityCursor | None:
        """Return the current opaque Activity high-water cursor."""


class SystemHealthReader(Protocol):
    def check(self, *, now: datetime) -> SystemHealthResponse:
        """Return passive local health evidence at the trusted check time."""


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
    activity_reader: ActivityReader | None = None,
    system_health_reader: SystemHealthReader | None = None,
    academic_reader: AcademicReader | None = None,
    document_reader: DocumentReader | None = None,
    rag_reader: AcademicRagReader | None = None,
    task_reader: TaskReader | None = None,
    task_commands: TaskApprovalCommands | None = None,
    principal_provider: LocalApproverContextProvider | None = None,
) -> APIRouter:
    """Create the versioned Phase 7 browser API router."""

    router = APIRouter(prefix="/api/v1")

    if activity_reader is not None:

        @router.get("/activity", response_model=ActivityHistoryResponse)
        def list_activity(
            limit: int = Query(default=50, ge=1, le=200),
            before: str | None = Query(default=None),
        ) -> ActivityHistoryResponse:
            cursor = None
            if before is not None:
                try:
                    cursor = decode_activity_cursor(before)
                except ValueError as error:
                    raise Phase7ApiError(
                        422,
                        "INVALID_REQUEST",
                        "The activity cursor is invalid.",
                    ) from error
            return activity_reader.list_history(limit=limit, before=cursor)

        @router.get("/activity/stream")
        async def stream_activity(
            request: Request,
            after: str | None = Query(default=None),
        ) -> StreamingResponse:
            raw_cursor = after if after is not None else request.headers.get("last-event-id")
            cursor = None
            if raw_cursor is not None:
                try:
                    cursor = decode_activity_cursor(raw_cursor)
                except ValueError as error:
                    raise Phase7ApiError(
                        422,
                        "INVALID_REQUEST",
                        "The activity cursor is invalid.",
                    ) from error
            return StreamingResponse(
                iter_activity_sse(
                    request,
                    activity_reader,
                    after=cursor,
                    sleep=asyncio.sleep,
                    clock=lambda: datetime.now(timezone.utc),
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-store",
                    "X-Accel-Buffering": "no",
                },
            )

    if system_health_reader is not None:

        @router.get("/system-health", response_model=SystemHealthResponse)
        def system_health() -> SystemHealthResponse:
            return system_health_reader.check(now=datetime.now(timezone.utc))

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

    if task_commands is not None and principal_provider is not None:

        def _enforce_decision_request(request: Request) -> None:
            try:
                enforce_mutation_fence(
                    host=request.headers.get("host"),
                    origin=request.headers.get("origin"),
                    content_type=request.headers.get("content-type"),
                )
            except MutationRequestRejected as error:
                raise Phase7ApiError(
                    403,
                    "MUTATION_NOT_ALLOWED",
                    "This approval decision is not allowed from the current request.",
                ) from error

        def _approver() -> TrustedApproverContext:
            from aaos.web.principal import PrincipalUnavailable

            try:
                return principal_provider.current()
            except PrincipalUnavailable as error:
                raise Phase7ApiError(
                    503,
                    "PRINCIPAL_UNAVAILABLE",
                    "AAOS could not identify the trusted local approver.",
                ) from error

        def _approval_id(value: str) -> ApprovalId:
            from aaos.approval.contracts import ApprovalId

            try:
                return ApprovalId(value)
            except (TypeError, ValueError) as error:
                raise Phase7ApiError(
                    422,
                    "INVALID_REQUEST",
                    "The approval identifier is invalid.",
                ) from error

        def _phase6_error(error: Phase6ErrorCode) -> Phase7ApiError:
            from aaos.approval.contracts import Phase6ErrorCode

            if error is Phase6ErrorCode.APPROVAL_NOT_FOUND:
                return Phase7ApiError(404, "APPROVAL_NOT_FOUND", "The approval is not available.")
            if error in {
                Phase6ErrorCode.APPROVAL_EXPIRED,
                Phase6ErrorCode.APPROVAL_REVOKED,
                Phase6ErrorCode.INVALID_STATE_TRANSITION,
            }:
                return Phase7ApiError(
                    409,
                    "APPROVAL_NOT_ACTIONABLE",
                    "The approval is no longer actionable.",
                )
            return Phase7ApiError(
                409,
                "APPROVAL_DECISION_REJECTED",
                "AAOS could not apply the approval decision.",
            )

        @router.post(
            "/approvals/{approval_id}/approve",
            response_model=ApprovalDetail,
        )
        def approve_approval(
            request: Request,
            approval_id: str,
            _body: ApprovalCommandRequest = Body(...),
        ) -> ApprovalDetail:
            _enforce_decision_request(request)
            try:
                from aaos.approval.service import Phase6ServiceError

                return task_commands.approve(
                    _approval_id(approval_id),
                    approver=_approver(),
                    now=datetime.now(timezone.utc),
                )
            except Phase6ServiceError as error:
                raise _phase6_error(error.code) from error

        @router.post(
            "/approvals/{approval_id}/reject",
            response_model=ApprovalDetail,
        )
        def reject_approval(
            request: Request,
            approval_id: str,
            _body: ApprovalCommandRequest = Body(...),
        ) -> ApprovalDetail:
            _enforce_decision_request(request)
            try:
                from aaos.approval.service import Phase6ServiceError

                return task_commands.reject(
                    _approval_id(approval_id),
                    approver=_approver(),
                    now=datetime.now(timezone.utc),
                )
            except Phase6ServiceError as error:
                raise _phase6_error(error.code) from error

    return router
