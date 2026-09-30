"""FastAPI application foundation for AAOS."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.study_tasks.contracts import StudyTask, StudyTaskPriority, StudyTaskStatus
from aaos.web.activity import ActivityCursor, ActivityEvent, ActivityHistoryResponse
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalSummary,
    AssignmentSummary,
    CourseSummary,
    DocumentSummary,
    RagAnswerResponse,
    RagQueryRequest,
    TaskDetail,
    TaskSummary,
)
from aaos.web.errors import install_error_boundary
from aaos.web.health import SystemHealthResponse

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from aaos.agent.academic_orchestrator import (
        AcademicAskResult,
        AcademicDocumentPort,
        AcademicReadPort,
        CalendarTaskProposalPort,
        MoodleChangeReadPort,
        ReadOnlyAcademicAssistant,
        StudyTaskPort,
    )
    from aaos.approval.contracts import ApprovalId, TrustedApproverContext
    from aaos.inference.lm_studio import LMStudioGateway
    from aaos.moodle.changes import MoodleChangeSet
    from aaos.rag.contracts import LocalRagService, RagQuery, RenderedRagResponse
    from aaos.rag.factory import ConfiguredPhase4Runtime
    from aaos.study_tasks.service import StudyTaskService
    from aaos.tasks.read_service import TaskReadService
    from aaos.tasks.service import TaskApprovalService
    from aaos.web.activity_read import ActivityReadService
    from aaos.web.health_read import SystemHealthService
    from aaos.web.principal import LocalApproverContextProvider
    from aaos.web.rag import AcademicRagReader, RagReadService
    from aaos.web.routes import (
        AcademicAskService,
        AcademicReader,
        ActivityReader,
        ApprovedCalendarTaskExecutor,
        CourseReader,
        DocumentReader,
        SystemHealthReader,
        TaskApprovalCommands,
        TaskReader,
    )

    class _AcademicDocumentReadDelegate(AcademicDocumentPort, Protocol):
        def get_document(self, document_id: int) -> DocumentSummary: ...


class HealthResponse(BaseModel):
    """Stable response contract for process-level health checks."""

    status: Literal["ok"]
    service: str
    version: str


class _LazyDefaultCourseReader:
    """Create default academic persistence only when courses are requested."""

    def __init__(self) -> None:
        self._delegate: AcademicReadPort | None = None
        self._document_delegate: _AcademicDocumentReadDelegate | None = None
        self._engine: Engine | None = None
        self._sessions: sessionmaker[Session] | None = None

    def _ensure_delegate(self) -> AcademicReadPort:
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
            self._sessions = sessions
            self._delegate = AcademicReadService(sessions)
            self._document_delegate = DocumentReadService(sessions)

        return self._delegate

    def _ensure_document_delegate(self) -> _AcademicDocumentReadDelegate:
        self._ensure_delegate()
        assert self._document_delegate is not None
        return self._document_delegate

    def list_courses(self, *, limit: int | None = None) -> tuple[CourseSummary, ...]:
        return self._ensure_delegate().list_courses(limit=limit)

    def get_course(self, course_id: int) -> CourseSummary:
        return self._ensure_delegate().get_course(course_id)

    def list_assignments(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        return self._ensure_delegate().list_assignments(course_id, limit=limit)

    def get_assignment(self, assignment_id: int) -> AssignmentSummary:
        return self._ensure_delegate().get_assignment(assignment_id)

    def list_documents(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[DocumentSummary, ...]:
        return self._ensure_document_delegate().list_documents(course_id, limit=limit)

    def get_document(self, document_id: int) -> DocumentSummary:
        return self._ensure_document_delegate().get_document(document_id)

    def session_factory(self) -> sessionmaker[Session]:
        self._ensure_delegate()
        assert self._sessions is not None
        return self._sessions

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultMoodleChangeReader:
    """Use the academic reader's existing lazy database lifecycle for history reads."""

    def __init__(self, academic_reader: _LazyDefaultCourseReader) -> None:
        self._academic_reader = academic_reader
        self._delegate: MoodleChangeReadPort | None = None

    def _ensure_delegate(self) -> MoodleChangeReadPort:
        if self._delegate is None:
            from aaos.moodle.change_read import MoodleChangeReadService

            self._delegate = MoodleChangeReadService(self._academic_reader.session_factory())
        return self._delegate

    def list_changes(
        self,
        *,
        course_id: int | None = None,
        since: datetime | None = None,
        limit: int = 15,
        offset: int = 0,
        query_time: datetime | None = None,
    ) -> MoodleChangeSet:
        return self._ensure_delegate().list_changes(
            course_id=course_id,
            since=since,
            limit=limit,
            offset=offset,
            query_time=query_time,
        )


class _LazyDefaultRagReader:
    """Create the Phase 4 RAG runtime only when the browser asks a question."""

    def __init__(self, sessions: sessionmaker[Session] | None = None) -> None:
        self._sessions = sessions
        self._engine: Engine | None = None
        self._runtime: ConfiguredPhase4Runtime | None = None
        self._delegate: RagReadService | None = None

    def _ensure_delegate(self) -> RagReadService:
        if self._delegate is None:
            from aaos.persistence.database import (
                create_database_engine,
                create_session_factory,
            )
            from aaos.rag.factory import build_configured_phase4_runtime
            from aaos.web.rag import RagReadService

            sessions = self._sessions
            if sessions is None:
                self._engine = create_database_engine()
                sessions = create_session_factory(self._engine)
            self._runtime = build_configured_phase4_runtime()
            self._delegate = RagReadService(self._runtime.service, sessions)
        return self._delegate

    async def ask(self, request: RagQueryRequest) -> RagAnswerResponse:
        return await self._ensure_delegate().ask(request)

    async def ask_trusted(self, query: RagQuery) -> RenderedRagResponse:
        self._ensure_delegate()
        assert self._runtime is not None
        return await self._runtime.service.ask(query)

    async def dispose(self) -> None:
        if self._runtime is not None:
            await self._runtime.aclose()
        if self._engine is not None:
            self._engine.dispose()


class _DefaultAgentRagReadService:
    """Expose the configured Phase 4 service through its trusted query contract."""

    def __init__(self, reader: _LazyDefaultRagReader) -> None:
        self._reader = reader

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        return await self._reader.ask_trusted(query)


class _LazyDefaultAcademicAskService:
    """Own one lazy local gateway and the bounded academic assistant."""

    def __init__(
        self,
        settings: AppSettings,
        academic_reader: AcademicReadPort,
        document_reader: AcademicDocumentPort,
        rag_service: LocalRagService,
        study_task_service: StudyTaskPort,
        calendar_task_service: CalendarTaskProposalPort | None,
        calendar_timezone: str | None,
        moodle_change_reader: MoodleChangeReadPort | None,
    ) -> None:
        self._settings = settings
        self._academic_reader = academic_reader
        self._document_reader = document_reader
        self._rag_service = rag_service
        self._study_task_service = study_task_service
        self._calendar_task_service = calendar_task_service
        self._calendar_timezone = calendar_timezone
        self._moodle_change_reader = moodle_change_reader
        self._gateway: LMStudioGateway | None = None
        self._assistant: ReadOnlyAcademicAssistant | None = None

    def _ensure_assistant(self) -> ReadOnlyAcademicAssistant:
        if self._assistant is None:
            import httpx2

            from aaos.agent.academic_orchestrator import ReadOnlyAcademicAssistant
            from aaos.inference.lm_studio import LMStudioGateway

            self._gateway = LMStudioGateway(self._settings, httpx2.AsyncHTTPTransport())
            self._assistant = ReadOnlyAcademicAssistant(
                self._gateway,
                self._academic_reader,
                self._document_reader,
                self._rag_service,
                study_task_service=self._study_task_service,
                calendar_task_service=self._calendar_task_service,
                calendar_timezone=self._calendar_timezone,
                moodle_change_reader=self._moodle_change_reader,
            )
        assert self._assistant is not None
        return self._assistant

    async def ask(
        self,
        question: str,
        cancel_event: asyncio.Event | None = None,
        *,
        conversation_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> AcademicAskResult:
        return await self._ensure_assistant().ask(
            question,
            cancel_event,
            conversation_id=conversation_id,
            request_id=request_id,
        )

    async def dispose(self) -> None:
        if self._gateway is not None:
            await self._gateway.aclose()


class _LazyDefaultStudyTaskService:
    """Create local StudyTask persistence only when an ask uses task tools."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._delegate: StudyTaskService | None = None

    def _ensure_delegate(self) -> StudyTaskService:
        if self._delegate is None:
            from aaos.persistence.database import create_database_engine, create_session_factory
            from aaos.study_tasks.repository import StudyTaskRepository
            from aaos.study_tasks.service import StudyTaskService

            self._engine = create_database_engine()
            self._delegate = StudyTaskService(
                StudyTaskRepository(create_session_factory(self._engine))
            )
        return self._delegate

    def list(
        self,
        *,
        status: StudyTaskStatus | None = None,
        course_id: int | None = None,
        limit: int = 50,
    ) -> tuple[StudyTask, ...]:
        return self._ensure_delegate().list(status=status, course_id=course_id, limit=limit)

    def get(self, task_id: int) -> StudyTask:
        return self._ensure_delegate().get(task_id)

    def create(
        self,
        *,
        request_id: UUID,
        title: str | None = None,
        course_id: int | None = None,
        assignment_id: int | None = None,
        priority: StudyTaskPriority = StudyTaskPriority.NORMAL,
    ) -> StudyTask:
        return self._ensure_delegate().create(
            request_id=request_id,
            title=title,
            course_id=course_id,
            assignment_id=assignment_id,
            priority=priority,
        )

    def update(
        self,
        task_id: int,
        *,
        request_id: UUID,
        title: str | None = None,
        priority: StudyTaskPriority | None = None,
    ) -> StudyTask:
        return self._ensure_delegate().update(
            task_id,
            request_id=request_id,
            title=title,
            priority=priority,
        )

    def complete(self, task_id: int, *, request_id: UUID) -> StudyTask:
        return self._ensure_delegate().complete(task_id, request_id=request_id)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultTaskReader:
    """Create task/approval persistence only when task reads are requested."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._delegate: TaskReadService | None = None

    def _ensure_delegate(self) -> TaskReadService:
        if self._delegate is None:
            from aaos.persistence.database import create_database_engine, create_session_factory
            from aaos.tasks.read_service import TaskReadService

            self._engine = create_database_engine()
            self._delegate = TaskReadService(create_session_factory(self._engine))
        return self._delegate

    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]:
        return self._ensure_delegate().list_tasks(limit=limit)

    def get_task(self, task_id: str) -> TaskDetail:
        return self._ensure_delegate().get_task(task_id)

    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]:
        return self._ensure_delegate().list_approvals(limit=limit)

    def get_approval(self, approval_id: str) -> ApprovalDetail:
        return self._ensure_delegate().get_approval(approval_id)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultTaskApprovalService:
    """Create approval decision persistence only when a decision is requested."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._delegate: TaskApprovalService | None = None

    def _ensure_delegate(self) -> TaskApprovalService:
        if self._delegate is None:
            from aaos.approval.repository import ApprovalRepository
            from aaos.persistence.database import create_database_engine, create_session_factory
            from aaos.tasks.read_service import TaskReadService
            from aaos.tasks.service import TaskApprovalService

            self._engine = create_database_engine()
            sessions = create_session_factory(self._engine)
            self._delegate = TaskApprovalService(
                ApprovalRepository(sessions), TaskReadService(sessions)
            )
        return self._delegate

    def approve(
        self, approval_id: ApprovalId, *, approver: TrustedApproverContext, now: datetime
    ) -> ApprovalDetail:
        return self._ensure_delegate().approve(approval_id, approver=approver, now=now)

    def reject(
        self, approval_id: ApprovalId, *, approver: TrustedApproverContext, now: datetime
    ) -> ApprovalDetail:
        return self._ensure_delegate().reject(approval_id, approver=approver, now=now)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultActivityReader:
    """Create Activity persistence only when history is requested."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._delegate: ActivityReadService | None = None

    def _ensure_delegate(self) -> ActivityReadService:
        if self._delegate is None:
            from aaos.persistence.database import create_database_engine, create_session_factory
            from aaos.web.activity_read import ActivityReadRepository, ActivityReadService

            self._engine = create_database_engine()
            self._delegate = ActivityReadService(
                ActivityReadRepository(create_session_factory(self._engine))
            )
        return self._delegate

    def list_history(
        self, *, limit: int, before: ActivityCursor | None = None
    ) -> ActivityHistoryResponse:
        return self._ensure_delegate().list_history(limit=limit, before=before)

    def tail(self, *, after: ActivityCursor | None, limit: int) -> tuple[ActivityEvent, ...]:
        return self._ensure_delegate().tail(after=after, limit=limit)

    def tail_with_cursors(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]:
        return self._ensure_delegate().tail_with_cursors(after=after, limit=limit)

    def current_high_watermark(self) -> ActivityCursor | None:
        return self._ensure_delegate().current_high_watermark()

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultSystemHealthReader:
    """Create passive health persistence only when detailed health is requested."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._delegate: SystemHealthService | None = None

    def _ensure_delegate(self) -> SystemHealthService:
        if self._delegate is None:
            from aaos.persistence.database import create_database_engine, create_session_factory
            from aaos.web.academic import AcademicReadService
            from aaos.web.health_read import SqlitePassiveRagIndexReader, SystemHealthService

            self._engine = create_database_engine()
            sessions = create_session_factory(self._engine)
            self._delegate = SystemHealthService(
                sessions,
                AcademicReadService(sessions),
                SqlitePassiveRagIndexReader(sessions),
            )
        return self._delegate

    def check(self, *, now: datetime) -> SystemHealthResponse:
        return self._ensure_delegate().check(now=now)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


class _LazyDefaultPrincipalProvider:
    """Resolve the configured local principal only when a decision is requested."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def current(self) -> TrustedApproverContext:
        from aaos.web.principal import LocalPrincipalProvider

        return LocalPrincipalProvider(self._settings).current()


def create_app(
    settings: AppSettings = DEFAULT_SETTINGS,
    *,
    session_factory: sessionmaker[Session] | None = None,
    rag_reader: AcademicRagReader | None = None,
    task_commands: TaskApprovalCommands | None = None,
    principal_provider: LocalApproverContextProvider | None = None,
    activity_reader: ActivityReader | None = None,
    system_health_reader: SystemHealthReader | None = None,
    ask_service: AcademicAskService | None = None,
    moodle_change_reader: MoodleChangeReadPort | None = None,
    study_task_service: StudyTaskPort | None = None,
    calendar_task_service: CalendarTaskProposalPort | None = None,
    calendar_task_executor: ApprovedCalendarTaskExecutor | None = None,
) -> FastAPI:
    """Create the AAOS API using explicit, non-secret configuration."""

    if calendar_task_service is not None and calendar_task_executor is None:
        raise ValueError("Calendar proposals require execution through the approval endpoint")

    application = FastAPI(
        title=settings.application_name,
        version=settings.application_version,
    )

    install_error_boundary(application)

    course_reader: CourseReader
    academic_reader: AcademicReader
    document_reader: DocumentReader
    task_reader: TaskReader
    resolved_task_commands: TaskApprovalCommands
    resolved_activity_reader: ActivityReader
    resolved_system_health_reader: SystemHealthReader
    resolved_principal_provider = principal_provider or _LazyDefaultPrincipalProvider(settings)
    resolved_rag_reader = rag_reader
    resolved_ask_service = ask_service
    resolved_moodle_change_reader = moodle_change_reader
    resolved_study_task_service = study_task_service

    if session_factory is None:
        lazy_course_reader = _LazyDefaultCourseReader()
        course_reader = lazy_course_reader
        academic_reader = lazy_course_reader
        document_reader = lazy_course_reader
        if resolved_moodle_change_reader is None:
            resolved_moodle_change_reader = _LazyDefaultMoodleChangeReader(lazy_course_reader)
        lazy_task_reader = _LazyDefaultTaskReader()
        task_reader = lazy_task_reader
        lazy_activity_reader = _LazyDefaultActivityReader()
        resolved_activity_reader = lazy_activity_reader
        lazy_system_health_reader = _LazyDefaultSystemHealthReader()
        resolved_system_health_reader = lazy_system_health_reader
        lazy_task_commands = task_commands or _LazyDefaultTaskApprovalService()
        resolved_task_commands = lazy_task_commands
        if resolved_study_task_service is None:
            lazy_study_tasks = _LazyDefaultStudyTaskService()
            resolved_study_task_service = lazy_study_tasks
            application.router.add_event_handler("shutdown", lazy_study_tasks.dispose)

        application.router.add_event_handler(
            "shutdown",
            lazy_course_reader.dispose,
        )
        application.router.add_event_handler("shutdown", lazy_task_reader.dispose)
        if isinstance(lazy_task_commands, _LazyDefaultTaskApprovalService):
            application.router.add_event_handler("shutdown", lazy_task_commands.dispose)
        application.router.add_event_handler("shutdown", lazy_activity_reader.dispose)
        application.router.add_event_handler("shutdown", lazy_system_health_reader.dispose)
    else:
        from aaos.approval.repository import ApprovalRepository
        from aaos.web.academic import AcademicReadService
        from aaos.web.activity_read import ActivityReadRepository, ActivityReadService
        from aaos.web.documents import DocumentReadService

        if resolved_moodle_change_reader is None:
            from aaos.moodle.change_read import MoodleChangeReadService

            resolved_moodle_change_reader = MoodleChangeReadService(session_factory)
        from aaos.web.health_read import SqlitePassiveRagIndexReader, SystemHealthService

        academic_reader = AcademicReadService(session_factory)
        course_reader = academic_reader
        document_reader = DocumentReadService(session_factory)
        from aaos.study_tasks.repository import StudyTaskRepository
        from aaos.study_tasks.service import StudyTaskService
        from aaos.tasks.read_service import TaskReadService
        from aaos.tasks.service import TaskApprovalService

        task_reader = TaskReadService(session_factory)
        if activity_reader is None:
            resolved_activity_reader = ActivityReadService(ActivityReadRepository(session_factory))
        else:
            resolved_activity_reader = activity_reader
        if system_health_reader is None:
            resolved_system_health_reader = SystemHealthService(
                session_factory,
                academic_reader,
                SqlitePassiveRagIndexReader(session_factory),
            )
        else:
            resolved_system_health_reader = system_health_reader
        resolved_task_commands = task_commands or TaskApprovalService(
            ApprovalRepository(session_factory),
            task_reader,
        )
        if resolved_study_task_service is None:
            resolved_study_task_service = StudyTaskService(StudyTaskRepository(session_factory))

    if resolved_rag_reader is None:
        lazy_rag_reader = _LazyDefaultRagReader(session_factory)
        resolved_rag_reader = lazy_rag_reader
        application.router.add_event_handler("shutdown", lazy_rag_reader.dispose)

    if resolved_ask_service is None:
        if isinstance(resolved_rag_reader, _LazyDefaultRagReader):
            agent_rag_reader = resolved_rag_reader
        else:
            agent_rag_reader = _LazyDefaultRagReader(session_factory)
            application.router.add_event_handler("shutdown", agent_rag_reader.dispose)
        resolved_ask_service = _LazyDefaultAcademicAskService(
            settings,
            academic_reader,
            document_reader,
            _DefaultAgentRagReadService(agent_rag_reader),
            resolved_study_task_service,
            calendar_task_service,
            settings.calendar_timezone,
            resolved_moodle_change_reader,
        )
        application.router.add_event_handler("shutdown", resolved_ask_service.dispose)

    # Imported here so module import stays inert: the route graph pulls in the
    # agent, RAG, and database stacks.
    from aaos.web.routes import create_api_v1_router

    application.include_router(
        create_api_v1_router(
            course_reader,
            activity_reader=resolved_activity_reader,
            system_health_reader=resolved_system_health_reader,
            academic_reader=academic_reader,
            document_reader=document_reader,
            rag_reader=resolved_rag_reader,
            task_reader=task_reader,
            task_commands=resolved_task_commands,
            principal_provider=resolved_principal_provider,
            ask_service=resolved_ask_service,
            calendar_task_executor=calendar_task_executor,
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


class _LazyASGIApplication:
    """Build the API on first use so importing this module stays inert."""

    def __init__(self) -> None:
        self._application: FastAPI | None = None

    def _get_application(self) -> FastAPI:
        if self._application is None:
            self._application = create_app()
        return self._application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._get_application()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_application(), name)


app = _LazyASGIApplication()
