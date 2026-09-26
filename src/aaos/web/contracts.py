"""Browser-safe Phase 7 API contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aaos.tasks.contracts import (
    BrowserTaskState,
    TaskFailureCode,
    TaskKind,
)


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class OperationState(StrEnum):
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    RECONCILIATION_PENDING = "RECONCILIATION_PENDING"


def _project_approval_state(value: object) -> ApprovalState:
    if isinstance(value, ApprovalState):
        return value
    if isinstance(value, StrEnum):
        return ApprovalState(value.value)
    return ApprovalState(cast(str, value))


def _project_operation_state(value: object) -> OperationState | None:
    if value is None:
        return None
    if isinstance(value, OperationState):
        return value
    if isinstance(value, StrEnum):
        return OperationState(value.value)
    return OperationState(cast(str, value))


class ApiErrorDetail(BaseModel):
    """Closed browser-safe error detail."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: str
    message: str
    request_id: str


class ApiErrorResponse(BaseModel):
    """Closed browser-safe error response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    error: ApiErrorDetail


class AcademicAskStatusView(StrEnum):
    ANSWERED = "ANSWERED"
    REFUSED = "REFUSED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class AcademicAskRequest(BaseModel):
    """Closed request contract for one bounded academic conversation turn."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    question: str = Field(min_length=1, max_length=4096)
    conversation_id: UUID | None = Field(default=None, strict=False)

    @field_validator("question")
    @classmethod
    def _question_is_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class AcademicAskSource(BaseModel):
    """Trusted academic source identity attached to a structured answer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tool_name: str = Field(min_length=1, max_length=64)
    entity_kind: str = Field(min_length=1, max_length=32)
    entity_id: int | None
    course_id: int | None = None
    title: str | None = Field(default=None, max_length=2048)
    query_filter: str | None = Field(default=None, max_length=4096)
    source_ref: str | None = Field(default=None, max_length=16)
    source_turn: int | None = Field(default=None, ge=1, le=8)


class AcademicAskResponse(BaseModel):
    """Bounded answer, refusal, or typed failure from read-only orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: AcademicAskStatusView
    answer_text: str | None = Field(default=None, max_length=16384)
    failure_code: str | None = Field(default=None, max_length=64)
    failure_message: str | None = Field(default=None, max_length=1024)
    refusal_code: str | None = Field(default=None, max_length=64)
    context_sources: tuple[AcademicAskSource, ...] = ()
    conversation_id: UUID | None = None
    sources: tuple[AcademicAskSource, ...] = ()
    rag_provenance: dict[str, object] | None = None
    step_count: int = Field(ge=0, le=8)


class CourseSummary(BaseModel):
    """Browser-safe projection of an active academic course."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: int
    title: str
    code: str | None
    description: str | None
    updated_at: datetime | None


class CourseListResponse(BaseModel):
    """Browser-safe course collection response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    courses: tuple[CourseSummary, ...]


class AssignmentSummary(BaseModel):
    """Browser-safe projection of one authoritative academic assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: int
    course_id: int
    title: str
    description: str | None
    due_at: datetime | None
    updated_at: datetime | None


class AssignmentListResponse(BaseModel):
    """Browser-safe assignment collection response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    assignments: tuple[AssignmentSummary, ...]


class DocumentIngestionStatusView(StrEnum):
    """Persisted document-ingestion states safe for browser projection."""

    NEVER_ATTEMPTED = "NEVER_ATTEMPTED"
    READY = "READY"
    FAILED = "FAILED"


class DocumentExtractionStatusView(StrEnum):
    """Persisted document-extraction states safe for browser projection."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NO_TEXT = "NO_TEXT"


class DocumentIndexReadinessView(StrEnum):
    """Persisted per-document index states safe for browser projection."""

    PREPARING = "PREPARING"
    READY = "READY"
    NOT_SEARCHABLE = "NOT_SEARCHABLE"
    FAILED = "FAILED"


class DocumentIndexCoverageView(StrEnum):
    """Persisted index coverage states safe for browser projection."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class DocumentSummary(BaseModel):
    """Browser-safe projection of document readiness metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: int
    document_version_id: int | None = Field(default=None, gt=0, exclude=True)
    course_id: int
    title: str
    ingestion_status: DocumentIngestionStatusView
    extraction_status: DocumentExtractionStatusView | None
    page_count: int | None
    index_readiness: DocumentIndexReadinessView | None
    index_coverage: DocumentIndexCoverageView | None
    updated_at: datetime


class DocumentListResponse(BaseModel):
    """Browser-safe document collection response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    documents: tuple[DocumentSummary, ...]


class RagGlobalScopeRequest(BaseModel):
    """Browser request selecting the trusted global academic scope."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["global"]


class RagCourseScopeRequest(BaseModel):
    """Browser request selecting one trusted active-course scope."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["course"]
    course_id: int = Field(gt=0)


RagScopeRequest = Annotated[
    RagGlobalScopeRequest | RagCourseScopeRequest,
    Field(discriminator="type"),
]


class RagQueryRequest(BaseModel):
    """Closed browser input for one read-only validated RAG query."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    question: str = Field(min_length=1, max_length=4096)
    scope: RagScopeRequest

    @field_validator("question")
    @classmethod
    def _question_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain text")
        return value


class RagCitation(BaseModel):
    """Safe public citation target resolved by trusted backend code."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    document_id: int = Field(gt=0)
    document_title: str = Field(min_length=1, max_length=500)
    page_start: int = Field(gt=0)
    page_end: int = Field(gt=0)

    @model_validator(mode="after")
    def _page_range_is_ordered(self) -> "RagCitation":
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self


class RagStructuralCitation(BaseModel):
    """Safe structural citation target resolved by trusted backend code."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    document_id: int = Field(gt=0)
    document_title: str = Field(
        min_length=1,
        max_length=500,
    )
    unit_start: int = Field(gt=0)
    unit_end: int = Field(gt=0)
    heading: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    table_index: int | None = Field(
        default=None,
        ge=0,
    )
    row_index: int | None = Field(
        default=None,
        ge=0,
    )
    column_index: int | None = Field(
        default=None,
        ge=0,
    )

    @model_validator(mode="after")
    def _structural_location_is_valid(
        self,
    ) -> "RagStructuralCitation":
        if self.unit_end < self.unit_start:
            raise ValueError("unit_end must not precede unit_start")

        if self.heading is not None and not self.heading.strip():
            raise ValueError("heading must contain text")

        coordinates = (
            self.table_index,
            self.row_index,
            self.column_index,
        )

        if any(value is None for value in coordinates) and not all(
            value is None for value in coordinates
        ):
            raise ValueError("table coordinates must be all present or all absent")

        return self


class RagAnswerStatusView(StrEnum):
    """Closed browser projection of Phase 4 answer statuses."""

    ANSWER = "ANSWER"
    PARTIAL_ANSWER = "PARTIAL_ANSWER"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RagAnswerResponse(BaseModel):
    """Validated final answer projection safe for browser rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: "RagAnswerStatusView"
    answer_text: str = Field(min_length=1, max_length=20000)
    language: str = Field(min_length=1, max_length=32)
    citations: tuple[
        RagCitation | RagStructuralCitation,
        ...,
    ]

    @model_validator(mode="after")
    def _insufficient_evidence_has_no_citations(self) -> "RagAnswerResponse":
        if self.status is RagAnswerStatusView.INSUFFICIENT_EVIDENCE and self.citations:
            raise ValueError("insufficient evidence cannot contain citations")
        return self


class SafeActionSummary(BaseModel):
    """Closed, human-readable calendar action summary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_type: str = Field(min_length=1, max_length=64)
    integration_label: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=200)
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=300)


class CalendarCreateTaskRequest(BaseModel):
    """Closed browser request for the only supported Phase 7E task kind."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: str = Field(min_length=1, max_length=200)
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=300)


class ApprovalCommandRequest(BaseModel):
    """Closed empty JSON body for approval decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TaskSummary(BaseModel):
    """Browser-safe durable task list projection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind
    status: BrowserTaskState
    created_at: datetime
    updated_at: datetime
    action: SafeActionSummary


class ApprovalSummary(BaseModel):
    """Browser-safe approval projection without trusted approver context."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    approval_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    action: SafeActionSummary
    status: ApprovalState
    issued_at: datetime
    approval_expires_at: datetime
    operation_state: OperationState | None
    can_approve: bool
    can_reject: bool

    @field_validator("status", mode="before")
    @classmethod
    def _accept_trusted_phase6_status(cls, value: object) -> object:
        return _project_approval_state(value)

    @field_validator("operation_state", mode="before")
    @classmethod
    def _accept_trusted_phase6_operation_state(cls, value: object) -> object:
        return _project_operation_state(value)


class TaskDetail(TaskSummary):
    """Browser-safe task detail with derived Phase 6 state only."""

    approval: ApprovalSummary | None
    operation_state: OperationState | None
    operation_outcome: str | None = Field(default=None, max_length=32)
    action_expires_at: datetime | None
    failure_code: TaskFailureCode | None

    @field_validator("operation_state", mode="before")
    @classmethod
    def _accept_trusted_phase6_operation_state(cls, value: object) -> object:
        return _project_operation_state(value)


class ApprovalDetail(ApprovalSummary):
    """Browser-safe approval detail with bounded decision metadata."""

    decided_at: datetime | None
    decision_reason: str | None = Field(default=None, max_length=64)


class TaskListResponse(BaseModel):
    """Browser-safe durable task collection response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tasks: tuple[TaskSummary, ...]


class ApprovalListResponse(BaseModel):
    """Browser-safe approval collection response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    approvals: tuple[ApprovalSummary, ...]
