"""Versioned browser-facing HTTP routes for AAOS Phase 7."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query, Request
from fastapi.responses import Response, StreamingResponse

from aaos.agent.academic_orchestrator import AcademicAskResult
from aaos.agent.study_plan_contracts import StudyPlan, StudyPlanSourceReference
from aaos.inference.selection import ModelSelectionError
from aaos.rag.inspection import INSPECTIONS, InspectionRecord
from aaos.tasks.contracts import BrowserTaskState
from aaos.web.activity import (
    ActivityCursor,
    ActivityEvent,
    ActivityHistoryResponse,
    decode_activity_cursor,
)
from aaos.web.activity_stream import iter_activity_sse
from aaos.web.ask_stream import iter_ask_sse
from aaos.web.contracts import (
    AcademicAskRagAnswerProvenanceView,
    AcademicAskRagProvenanceSummaryView,
    AcademicAskRequest,
    AcademicAskResponse,
    AcademicAskSource,
    AcademicAskStatusView,
    ApprovalCommandRequest,
    ApprovalDetail,
    ApprovalListResponse,
    ApprovalState,
    ApprovalSummary,
    AssignmentListResponse,
    AssignmentSummary,
    CalendarProposalView,
    ChatGPTPlanUsageView,
    CitationEvidenceView,
    CourseListResponse,
    CourseSummary,
    DocumentListResponse,
    DocumentSummary,
    ModelOptionsResponse,
    ModelSelectRequest,
    ModelStatusResponse,
    OperationState,
    PlanFactKindView,
    RagAnswerResponse,
    RagQueryRequest,
    StudyActivityTypeView,
    StudyPlanItemView,
    StudyPlanMoodleChangeView,
    StudyPlanObligationView,
    StudyPlanSourceReferenceView,
    StudyPlanTaskFactView,
    StudyPlanView,
    StudyRationaleCodeView,
    StudyTaskListResponse,
    TaskDetail,
    TaskListResponse,
    TaskSummary,
)
from aaos.web.errors import Phase7ApiError
from aaos.web.health import SystemHealthResponse
from aaos.web.inspection import enrich_inspection
from aaos.web.mutation_security import MutationRequestRejected, enforce_mutation_fence

if TYPE_CHECKING:
    from aaos.approval.contracts import ApprovalId, Phase6ErrorCode, TrustedApproverContext
    from aaos.inference.selection import ModelStatus
    from aaos.study_tasks.contracts import StudyTask
    from aaos.web.document_files import DocumentFile
    from aaos.web.principal import LocalApproverContextProvider
    from aaos.web.rag import AcademicRagReader


# (code, HTTP status, browser-safe message) for each refused model switch.
_MODEL_SELECTION_FAILURES = {
    "INVALID_PROVIDER": (422, "INVALID_REQUEST", "The request is invalid."),
    "CLOUD_CONFIRMATION_REQUIRED": (
        422,
        "CLOUD_CONFIRMATION_REQUIRED",
        "Confirm that your questions and file excerpts may be sent to the cloud provider.",
    ),
    "GEMINI_NOT_READY": (
        409,
        "GEMINI_NOT_READY",
        "Gemini is not set up in the backend's environment yet.",
    ),
    "CHATGPT_PLAN_NOT_READY": (
        409,
        "CHATGPT_PLAN_NOT_READY",
        "Sign in first: run 'aaos model chatgpt-plan sign-in' in a terminal.",
    ),
    "MODEL_BUSY": (409, "MODEL_BUSY", "A question is running. Try again when it has finished."),
    "MODEL_SWITCH_FAILED": (500, "MODEL_SWITCH_FAILED", "AAOS could not switch the model."),
    "INVALID_MODEL": (
        422,
        "INVALID_MODEL",
        "That is not one of the models available to you.",
    ),
    "MODEL_LIST_FAILED": (
        503,
        "MODEL_LIST_FAILED",
        "AAOS could not get the list of models.",
    ),
}


class ModelSelectionPort(Protocol):
    """Which model answers, and the guarded switch between local and the cloud models."""

    def status(self) -> ModelStatus: ...

    async def switch(
        self,
        provider: str,
        *,
        confirm_cloud: bool = False,
        gemini_model: str | None = None,
        chatgpt_plan_model: str | None = None,
    ) -> ModelStatus: ...

    async def gemini_models(self) -> tuple[str, ...]: ...

    async def chatgpt_plan_models(self) -> tuple[str, ...]: ...


class DocumentFileReader(Protocol):
    """The original file of one visible document."""

    def open_document_file(self, document_id: int) -> DocumentFile: ...


class CitationEvidenceReader(Protocol):
    def read_evidence(self, version_id: int, start: int, end: int) -> CitationEvidenceView: ...

    def open_document_version_file(self, version_id: int) -> DocumentFile: ...


class StudyTaskReader(Protocol):
    """Read-only access to local study tasks."""

    def list(self) -> tuple[StudyTask, ...]: ...


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


class ApprovedCalendarTaskExecutor(Protocol):
    """Execute only a task linked to a trusted approval decision."""

    def execute_approved_task(self, task_id: str, *, now: datetime) -> TaskDetail: ...


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


class AcademicAskService(Protocol):
    """Bounded read-only question service exposed through the Ask route."""

    async def ask(
        self,
        question: str,
        cancel_event: asyncio.Event | None = None,
        *,
        conversation_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> AcademicAskResult: ...


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
    ask_service: AcademicAskService | None = None,
    calendar_task_executor: ApprovedCalendarTaskExecutor | None = None,
    study_task_reader: StudyTaskReader | None = None,
    document_file_reader: DocumentFileReader | None = None,
    model_selection: ModelSelectionPort | None = None,
    evidence_reader: CitationEvidenceReader | None = None,
) -> APIRouter:
    """Create the versioned Phase 7 browser API router."""

    router = APIRouter(prefix="/api/v1")

    if calendar_task_executor is not None and task_reader is None:
        raise ValueError("Calendar execution after approval requires a task reader")

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

    if ask_service is not None:

        async def execute_ask(
            body: AcademicAskRequest, cancellation: asyncio.Event
        ) -> AcademicAskResponse:
            records: list[InspectionRecord] = []
            token = INSPECTIONS.set(records)
            try:
                result = await ask_service.ask(
                    body.question,
                    cancellation,
                    conversation_id=body.conversation_id,
                    request_id=body.request_id,
                )
                return _academic_ask_response(result, body.request_id, records)
            finally:
                INSPECTIONS.reset(token)

        @router.post("/ask", response_model=AcademicAskResponse)
        async def ask_academic(
            request: Request,
            body: AcademicAskRequest,
        ) -> AcademicAskResponse:
            cancellation = asyncio.Event()
            ask_task = asyncio.create_task(execute_ask(body, cancellation))

            async def monitor_disconnect() -> None:
                while not ask_task.done():
                    if await request.is_disconnected():
                        cancellation.set()
                        return
                    await asyncio.sleep(0.1)

            monitor_task = asyncio.create_task(monitor_disconnect())
            try:
                response = await ask_task
            finally:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)

            return response

        @router.post(
            "/ask/stream",
            response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}}},
        )
        async def ask_academic_stream(
            request: Request,
            body: AcademicAskRequest,
        ) -> StreamingResponse:
            """Same request and final answer as ``/ask``, delivered as SSE."""

            async def run(cancellation: asyncio.Event) -> AcademicAskResponse:
                return await execute_ask(body, cancellation)

            return StreamingResponse(
                iter_ask_sse(request, run),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-store",
                    "X-Accel-Buffering": "no",
                },
            )

    if evidence_reader is not None:

        @router.get("/document-versions/{version_id}/evidence", response_model=CitationEvidenceView)
        def get_citation_evidence(
            response: Response,
            version_id: int = Path(..., gt=0),
            start: int = Query(..., gt=0),
            end: int = Query(..., gt=0),
        ) -> CitationEvidenceView:
            response.headers["Cache-Control"] = "no-store"
            return evidence_reader.read_evidence(version_id, start, end)

        @router.get("/document-versions/{version_id}/file")
        def get_cited_file(version_id: int = Path(..., gt=0)) -> Response:
            document = evidence_reader.open_document_version_file(version_id)
            return Response(
                content=document.body,
                media_type=document.media_type,
                headers={
                    "Content-Disposition": "inline"
                    if document.media_type == "application/pdf"
                    else "attachment",
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )

    if document_file_reader is not None:

        @router.get("/documents/{document_id}/file")
        def get_document_file(document_id: int = Path(..., gt=0)) -> Response:
            """The original file: PDFs open in the browser, Word files download."""
            from urllib.parse import quote

            document = document_file_reader.open_document_file(document_id)
            disposition = "inline" if document.media_type == "application/pdf" else "attachment"
            ascii_name = document.filename.encode("ascii", "replace").decode().replace('"', "")
            return Response(
                content=document.body,
                media_type=document.media_type,
                headers={
                    "Content-Disposition": (
                        f'{disposition}; filename="{ascii_name}"; '
                        f"filename*=UTF-8''{quote(document.filename)}"
                    ),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, max-age=300",
                },
            )

    if model_selection is not None:

        def _model_response(status: ModelStatus) -> ModelStatusResponse:
            usage = status.chatgpt_plan_usage
            return ModelStatusResponse(
                provider=status.provider,  # type: ignore[arg-type]
                model=status.model,
                cloud=status.cloud,
                local_model=status.local_model,
                gemini_model=status.gemini_model,
                gemini_ready=status.gemini_ready,
                gemini_blocker=status.gemini_blocker,
                last_failure=status.last_failure,
                chatgpt_plan_model=status.chatgpt_plan_model,
                chatgpt_plan_ready=status.chatgpt_plan_ready,
                chatgpt_plan_blocker=status.chatgpt_plan_blocker,
                chatgpt_plan_usage=(
                    ChatGPTPlanUsageView(
                        since=usage.since,
                        replies=usage.replies,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        limit_reached_at=usage.limit_reached_at,
                    )
                    if usage is not None
                    else None
                ),
            )

        @router.get("/model", response_model=ModelStatusResponse)
        def get_model() -> ModelStatusResponse:
            """Which model is answering, read-only."""
            return _model_response(model_selection.status())

        @router.get("/model/gemini-models", response_model=ModelOptionsResponse)
        async def get_gemini_models() -> ModelOptionsResponse:
            """The Gemini chat models the configured key can use (names only)."""
            try:
                return ModelOptionsResponse(models=await model_selection.gemini_models())
            except ModelSelectionError as error:
                http_status, code, message = _MODEL_SELECTION_FAILURES[error.code]
                raise Phase7ApiError(http_status, code, message) from None

        @router.get("/model/chatgpt-plan-models", response_model=ModelOptionsResponse)
        async def get_chatgpt_plan_models() -> ModelOptionsResponse:
            """The models the signed-in ChatGPT plan offers (names only)."""
            try:
                return ModelOptionsResponse(models=await model_selection.chatgpt_plan_models())
            except ModelSelectionError as error:
                http_status, code, message = _MODEL_SELECTION_FAILURES[error.code]
                raise Phase7ApiError(http_status, code, message) from None

        @router.post("/model", response_model=ModelStatusResponse)
        async def select_model(request: Request, body: ModelSelectRequest) -> ModelStatusResponse:
            """Switch between the local model and a cloud model (cloud needs confirmation)."""
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
                    "The model can only be changed from the local AAOS page.",
                ) from error
            try:
                status = await model_selection.switch(
                    body.provider,
                    confirm_cloud=body.confirm_cloud,
                    gemini_model=body.gemini_model,
                    chatgpt_plan_model=body.chatgpt_plan_model,
                )
            except ModelSelectionError as error:
                http_status, code, message = _MODEL_SELECTION_FAILURES[error.code]
                raise Phase7ApiError(http_status, code, message) from None
            return _model_response(status)

    if study_task_reader is not None:

        @router.get("/study-tasks", response_model=StudyTaskListResponse)
        def list_study_tasks() -> StudyTaskListResponse:
            """Local study tasks, read-only."""
            from aaos.web.study_tasks import study_task_list_response

            return study_task_list_response(study_task_reader.list())

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

                now = datetime.now(timezone.utc)
                approval = task_commands.approve(
                    _approval_id(approval_id),
                    approver=_approver(),
                    now=now,
                )
                if (
                    calendar_task_executor is not None
                    and approval.status is ApprovalState.APPROVED
                    and approval.action.action_type == "CREATE_EVENT"
                ):
                    execution_now = datetime.now(timezone.utc)
                    calendar_task_executor.execute_approved_task(
                        approval.task_id,
                        now=execution_now,
                    )
                    assert task_reader is not None
                    return task_reader.get_approval(approval.approval_id)
                return approval
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


def _json_compatible_mapping(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None

    def convert(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): convert(nested) for key, nested in item.items()}
        if isinstance(item, (tuple, list)):
            return [convert(nested) for nested in item]
        if hasattr(item, "value") and isinstance(getattr(item, "value"), str):
            return getattr(item, "value")
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        raise TypeError("academic answer provenance is not JSON-compatible")

    normalized = convert(value)
    if not isinstance(normalized, dict):
        raise TypeError("academic answer provenance must be an object")
    return normalized


def _academic_ask_response(
    result: AcademicAskResult,
    request_id: UUID | None,
    records: list[InspectionRecord] | None = None,
) -> AcademicAskResponse:
    return AcademicAskResponse(
        status=AcademicAskStatusView(result.status.value),
        answer_text=result.answer_text,
        failure_code=(result.failure_code.value if result.failure_code is not None else None),
        failure_message=result.failure_message,
        refusal_code=(result.refusal_code.value if result.refusal_code is not None else None),
        sources=tuple(
            AcademicAskSource(
                tool_name=source.tool_name,
                entity_kind=source.entity_kind,
                entity_id=source.entity_id,
                course_id=source.course_id,
                title=source.title,
                query_filter=source.query_filter,
                source_ref=source.source_ref,
                source_turn=source.source_turn,
                change_id=source.change_id,
                from_snapshot_id=source.from_snapshot_id,
                to_snapshot_id=source.to_snapshot_id,
                changed_fields=source.changed_fields,
                change_kind=source.change_kind,
                before_due_at=source.before_due_at,
                after_due_at=source.after_due_at,
                study_task_status=source.study_task_status,
                study_task_priority=source.study_task_priority,
                study_task_due_at=source.study_task_due_at,
                study_task_course_id=source.study_task_course_id,
                study_task_course_title=source.study_task_course_title,
                study_task_assignment_id=source.study_task_assignment_id,
                study_task_assignment_title=source.study_task_assignment_title,
            )
            for source in result.sources
        ),
        context_sources=tuple(
            AcademicAskSource(
                tool_name=source.tool_name,
                entity_kind=source.entity_kind,
                entity_id=source.entity_id,
                course_id=source.course_id,
                title=source.title,
                query_filter=source.query_filter,
                source_ref=source.source_ref,
                source_turn=source.source_turn,
                change_id=source.change_id,
                from_snapshot_id=source.from_snapshot_id,
                to_snapshot_id=source.to_snapshot_id,
                changed_fields=source.changed_fields,
                change_kind=source.change_kind,
                before_due_at=source.before_due_at,
                after_due_at=source.after_due_at,
                study_task_status=source.study_task_status,
                study_task_priority=source.study_task_priority,
                study_task_due_at=source.study_task_due_at,
                study_task_course_id=source.study_task_course_id,
                study_task_course_title=source.study_task_course_title,
                study_task_assignment_id=source.study_task_assignment_id,
                study_task_assignment_title=source.study_task_assignment_title,
            )
            for source in result.context_sources
        ),
        rag_provenance=enrich_inspection(
            _academic_ask_rag_provenance_view(result.rag_provenance), records or []
        ),
        calendar_proposal=(
            CalendarProposalView(
                proposal_id=result.calendar_proposal.proposal_id,
                approval_id=result.calendar_proposal.approval_id,
                operation=result.calendar_proposal.operation,
                summary=result.calendar_proposal.summary,
                start=result.calendar_proposal.start,
                end=result.calendar_proposal.end,
                timezone=result.calendar_proposal.timezone,
                source_entity_kind=result.calendar_proposal.source_entity_kind,
                source_entity_id=result.calendar_proposal.source_entity_id,
                approval_status=ApprovalState(result.calendar_proposal.approval_status),
                task_status=BrowserTaskState(result.calendar_proposal.task_status),
                operation_state=(
                    OperationState(result.calendar_proposal.operation_state)
                    if result.calendar_proposal.operation_state is not None
                    else None
                ),
            )
            if result.calendar_proposal is not None
            else None
        ),
        study_plan=_study_plan_view(result.study_plan),
        step_count=result.step_count,
        conversation_id=result.conversation_id,
        request_id=request_id,
    )


def _academic_ask_rag_provenance_view(
    value: Mapping[str, object] | None,
) -> AcademicAskRagAnswerProvenanceView | AcademicAskRagProvenanceSummaryView | None:
    normalized = _json_compatible_mapping(value)
    if normalized is None:
        return None
    if "citations" in normalized:
        citations = normalized.get("citations")
        if not isinstance(citations, list):
            raise TypeError("academic RAG citations must be a list")
        normalized["citations"] = tuple(citations)
        return AcademicAskRagAnswerProvenanceView.model_validate(normalized)
    return AcademicAskRagProvenanceSummaryView.model_validate(normalized)


def _study_plan_view(plan: StudyPlan | None) -> StudyPlanView | None:
    if plan is None:
        return None

    def source_view(source: StudyPlanSourceReference) -> StudyPlanSourceReferenceView:
        return StudyPlanSourceReferenceView(
            kind=PlanFactKindView(source.kind.value),
            tool_name=cast(
                Literal[
                    "academic.list_assignments",
                    "study_task.list",
                    "moodle.list_changes",
                    "rag.answer",
                ],
                source.tool_name,
            ),
            entity_id=source.entity_id,
            course_id=source.course_id,
            change_id=source.change_id,
            from_snapshot_id=source.from_snapshot_id,
            to_snapshot_id=source.to_snapshot_id,
            document_version_id=source.document_version_id,
            citation_location=source.citation_location,
            claim_id=source.claim_id,
        )

    return StudyPlanView(
        plan_id=UUID(plan.plan_id),
        planning_window_start=plan.planning_window_start,
        planning_window_end=plan.planning_window_end,
        timezone=plan.timezone,
        generated_at=plan.generated_at,
        calendar_conflict_aware=plan.calendar_conflict_aware,
        assumptions=plan.assumptions,
        warnings=plan.warnings,
        obligations=tuple(
            StudyPlanObligationView(
                assignment_id=item.assignment_id,
                course_id=item.course_id,
                title=item.title,
                due_at=item.due_at,
                overdue=item.overdue,
                source=source_view(item.source),
            )
            for item in plan.obligations
        ),
        study_tasks=tuple(
            StudyPlanTaskFactView(
                task_id=item.task_id,
                title=item.title,
                status=cast(Literal["OPEN", "COMPLETED"], item.status),
                priority=cast(Literal["LOW", "NORMAL", "HIGH"], item.priority),
                course_id=item.course_id,
                assignment_id=item.assignment_id,
                due_at=item.due_at,
                source=source_view(item.source),
            )
            for item in plan.study_tasks
        ),
        moodle_changes=tuple(
            StudyPlanMoodleChangeView(
                change_id=item.change_id,
                assignment_id=item.assignment_id,
                course_id=item.course_id,
                change_kind=cast(Literal["ADDED", "REMOVED", "MODIFIED"], item.change_kind),
                title=item.title,
                changed_fields=cast(
                    tuple[
                        Literal[
                            "title",
                            "code",
                            "due_at",
                            "published_at",
                            "course",
                            "document_version",
                        ],
                        ...,
                    ],
                    item.changed_fields,
                ),
                before_due_at=item.before_due_at,
                after_due_at=item.after_due_at,
                from_snapshot_id=item.from_snapshot_id,
                to_snapshot_id=item.to_snapshot_id,
                detected_at=item.detected_at,
                source=source_view(item.source),
            )
            for item in plan.moodle_changes
        ),
        items=tuple(
            StudyPlanItemView(
                study_date=item.study_date,
                course_id=item.course_id,
                assignment_id=item.assignment_id,
                study_task_id=item.study_task_id,
                activity_type=StudyActivityTypeView(item.activity_type.value),
                duration_minutes=item.duration_minutes,
                rationale_code=StudyRationaleCodeView(item.rationale_code.value),
                focus=item.focus,
                estimated_duration=item.estimated_duration,
                source_refs=tuple(source_view(source) for source in item.source_refs),
            )
            for item in plan.items
        ),
        available_minutes=plan.available_minutes,
        max_session_minutes=plan.max_session_minutes,
        moodle_from_snapshot_id=plan.moodle_from_snapshot_id,
        moodle_to_snapshot_id=plan.moodle_to_snapshot_id,
        rag_provenance=tuple(source_view(source) for source in plan.rag_provenance),
    )
