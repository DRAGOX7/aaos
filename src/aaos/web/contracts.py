"""Browser-safe Phase 7 API contracts."""

from datetime import datetime

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
