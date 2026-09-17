"""Browser-safe Phase 7 API contracts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
