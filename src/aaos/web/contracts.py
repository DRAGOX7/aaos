"""Browser-safe Phase 7 API contracts."""

from datetime import datetime
from typing import Annotated, Literal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aaos.rag.contracts import RagAnswerStatus


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


class RagAnswerResponse(BaseModel):
    """Validated final answer projection safe for browser rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: RagAnswerStatus
    answer_text: str = Field(min_length=1, max_length=20000)
    language: str = Field(min_length=1, max_length=32)
    citations: tuple[RagCitation, ...]

    @model_validator(mode="after")
    def _insufficient_evidence_has_no_citations(self) -> "RagAnswerResponse":
        if self.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE and self.citations:
            raise ValueError("insufficient evidence cannot contain citations")
        return self
