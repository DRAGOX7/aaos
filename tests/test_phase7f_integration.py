from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.activity import (
    ActivityCursor,
    ActivityEvent,
    ActivityHistoryResponse,
)
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalSummary,
    AssignmentSummary,
    CourseSummary,
    DocumentIngestionStatusView,
    DocumentSummary,
    RagAnswerResponse,
    RagAnswerStatusView,
    RagQueryRequest,
    TaskDetail,
    TaskSummary,
)
from aaos.web.errors import install_error_boundary
from aaos.web.health import (
    HealthComponent,
    HealthComponentName,
    HealthDetailCode,
    HealthStatus,
    SystemHealthResponse,
)
from aaos.web.routes import create_api_v1_router

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


class IntegratedAcademicReader:
    def list_courses(self) -> tuple[CourseSummary, ...]:
        return (CourseSummary(id=1, title="AI", code="AI-1", description=None, updated_at=NOW),)

    def get_course(self, course_id: int) -> CourseSummary:
        return self.list_courses()[0]

    def list_assignments(self, course_id: int | None = None) -> tuple[AssignmentSummary, ...]:
        return ()


class IntegratedDocumentReader:
    def list_documents(self, course_id: int | None = None) -> tuple[DocumentSummary, ...]:
        return ()

    def get_document(self, document_id: int) -> DocumentSummary:
        return DocumentSummary(
            id=document_id,
            course_id=1,
            title="Notes",
            ingestion_status=DocumentIngestionStatusView.NEVER_ATTEMPTED,
            extraction_status=None,
            page_count=None,
            index_readiness=None,
            index_coverage=None,
            updated_at=NOW,
        )


class IntegratedActivityReader:
    def list_history(
        self, *, limit: int, before: ActivityCursor | None = None
    ) -> ActivityHistoryResponse:
        return ActivityHistoryResponse(
            schema_version="phase7-activity-v1", events=(), next_cursor=None
        )

    def tail(self, *, after: ActivityCursor | None, limit: int) -> tuple[ActivityEvent, ...]:
        return ()

    def tail_with_cursors(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]:
        return ()

    def current_high_watermark(self) -> ActivityCursor | None:
        return None


class IntegratedHealthReader:
    def check(self, *, now: datetime) -> SystemHealthResponse:
        return SystemHealthResponse(
            schema_version="phase7-health-v1",
            checked_at=now,
            overall=HealthStatus.HEALTHY,
            components=(
                HealthComponent(
                    name=HealthComponentName.DATABASE,
                    status=HealthStatus.HEALTHY,
                    required=True,
                    detail_code=HealthDetailCode.RAG_INDEX_READY,
                ),
            ),
        )


class IntegratedRagReader:
    async def ask(self, request: RagQueryRequest) -> RagAnswerResponse:
        return RagAnswerResponse(
            status=RagAnswerStatusView.INSUFFICIENT_EVIDENCE,
            answer_text="No supported evidence.",
            language="en",
            citations=(),
        )


class IntegratedTaskReader:
    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]:
        return ()

    def get_task(self, task_id: str) -> TaskDetail:
        raise AssertionError("detail route is not part of the composition probe")

    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]:
        return ()

    def get_approval(self, approval_id: str) -> ApprovalDetail:
        raise AssertionError("detail route is not part of the composition probe")


def _app() -> FastAPI:
    application = FastAPI()
    install_error_boundary(application)
    academic = IntegratedAcademicReader()
    application.include_router(
        create_api_v1_router(
            academic,
            academic_reader=academic,
            document_reader=IntegratedDocumentReader(),
            activity_reader=IntegratedActivityReader(),
            system_health_reader=IntegratedHealthReader(),
            rag_reader=IntegratedRagReader(),
            task_reader=IntegratedTaskReader(),
        )
    )
    return application


def test_activity_and_health_compose_with_existing_read_surfaces() -> None:
    application = _app()
    client = TestClient(application)

    for path in (
        "/api/v1/courses",
        "/api/v1/courses/1",
        "/api/v1/assignments",
        "/api/v1/documents",
        "/api/v1/documents/1",
        "/api/v1/tasks",
        "/api/v1/approvals",
        "/api/v1/activity",
        "/api/v1/system-health",
    ):
        assert client.get(path).status_code == 200, path

    paths = set(application.openapi()["paths"])
    assert "/api/v1/rag/queries" in paths
    assert "/api/v1/activity/stream" in paths


def test_new_read_surfaces_are_get_only() -> None:
    client = TestClient(_app())
    for path in (
        "/api/v1/activity",
        "/api/v1/activity/stream",
        "/api/v1/system-health",
    ):
        assert client.post(path).status_code == 405
        assert client.put(path).status_code == 405
        assert client.patch(path).status_code == 405
        assert client.delete(path).status_code == 405
