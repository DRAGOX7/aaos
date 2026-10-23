"""Closed browser-safe System Health contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class HealthComponentName(StrEnum):
    DATABASE = "DATABASE"
    ACADEMIC_DATA = "ACADEMIC_DATA"
    ACADEMIC_SYNC = "ACADEMIC_SYNC"
    DOCUMENT_PIPELINE = "DOCUMENT_PIPELINE"
    RAG_INDEX = "RAG_INDEX"
    INFERENCE = "INFERENCE"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"


class HealthDetailCode(StrEnum):
    DATABASE_READY = "DATABASE_READY"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SCHEMA_NOT_READY = "SCHEMA_NOT_READY"
    ACADEMIC_DATA_READY = "ACADEMIC_DATA_READY"
    ACADEMIC_READ_UNAVAILABLE = "ACADEMIC_READ_UNAVAILABLE"
    RAG_INDEX_READY = "RAG_INDEX_READY"
    RAG_INDEX_PARTIAL = "RAG_INDEX_PARTIAL"
    RAG_INDEX_NOT_CONFIGURED = "RAG_INDEX_NOT_CONFIGURED"
    PASSIVE_STATUS_UNAVAILABLE = "PASSIVE_STATUS_UNAVAILABLE"
    INTERNAL_STATUS_UNAVAILABLE = "INTERNAL_STATUS_UNAVAILABLE"
    # Phase 12 (additive): everyday Calendar proposals are configured, or switched on
    # with a missing or invalid setting (and therefore off).
    CALENDAR_PROPOSALS_READY = "CALENDAR_PROPOSALS_READY"
    CALENDAR_PROPOSALS_MISCONFIGURED = "CALENDAR_PROPOSALS_MISCONFIGURED"


class HealthComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: HealthComponentName
    status: HealthStatus
    required: bool
    detail_code: HealthDetailCode


class SystemHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["phase7-health-v1"]
    checked_at: datetime
    overall: HealthStatus
    components: tuple[HealthComponent, ...]
