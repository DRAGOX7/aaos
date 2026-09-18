"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from aaos.config import DEFAULT_SETTINGS, AppSettings
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
from aaos.web.routes import (
    AcademicReader,
    CourseReader,
    DocumentReader,
    TaskApprovalCommands,
    TaskReader,
    create_api_v1_router,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from aaos.approval.contracts import ApprovalId, TrustedApproverContext
    from aaos.rag.factory import ConfiguredPhase4Runtime
    from aaos.tasks.read_service import TaskReadService
    from aaos.tasks.service import TaskApprovalService
    from aaos.web.principal import LocalApproverContextProvider
    from aaos.web.rag import AcademicRagReader, RagReadService


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

    async def dispose(self) -> None:
        if self._runtime is not None:
            await self._runtime.aclose()
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
    task_reader: TaskReader
    resolved_task_commands: TaskApprovalCommands
    resolved_principal_provider = principal_provider or _LazyDefaultPrincipalProvider(settings)
    resolved_rag_reader = rag_reader

    if session_factory is None:
        lazy_course_reader = _LazyDefaultCourseReader()
        course_reader = lazy_course_reader
        academic_reader = lazy_course_reader
        document_reader = lazy_course_reader
        lazy_task_reader = _LazyDefaultTaskReader()
        task_reader = lazy_task_reader
        lazy_task_commands = task_commands or _LazyDefaultTaskApprovalService()
        resolved_task_commands = lazy_task_commands

        application.router.add_event_handler(
            "shutdown",
            lazy_course_reader.dispose,
        )
        application.router.add_event_handler("shutdown", lazy_task_reader.dispose)
        if isinstance(lazy_task_commands, _LazyDefaultTaskApprovalService):
            application.router.add_event_handler("shutdown", lazy_task_commands.dispose)
    else:
        from aaos.approval.repository import ApprovalRepository
        from aaos.web.academic import AcademicReadService
        from aaos.web.documents import DocumentReadService

        academic_reader = AcademicReadService(session_factory)
        course_reader = academic_reader
        document_reader = DocumentReadService(session_factory)
        from aaos.tasks.read_service import TaskReadService
        from aaos.tasks.service import TaskApprovalService

        task_reader = TaskReadService(session_factory)
        resolved_task_commands = task_commands or TaskApprovalService(
            ApprovalRepository(session_factory),
            task_reader,
        )

    if resolved_rag_reader is None:
        lazy_rag_reader = _LazyDefaultRagReader(session_factory)
        resolved_rag_reader = lazy_rag_reader
        application.router.add_event_handler("shutdown", lazy_rag_reader.dispose)

    application.include_router(
        create_api_v1_router(
            course_reader,
            academic_reader=academic_reader,
            document_reader=document_reader,
            rag_reader=resolved_rag_reader,
            task_reader=task_reader,
            task_commands=resolved_task_commands,
            principal_provider=resolved_principal_provider,
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
