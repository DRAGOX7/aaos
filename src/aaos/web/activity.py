"""Closed browser-safe Activity projections and opaque cursors."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field


class ActivityCategory(StrEnum):
    TASK = "TASK"
    APPROVAL = "APPROVAL"
    INTEGRATION = "INTEGRATION"
    SYSTEM = "SYSTEM"


class ActivityEventCode(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    ACTION_PREPARED = "ACTION_PREPARED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    RECONCILED = "RECONCILED"


class ActivityStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ActivityResource(StrEnum):
    TASK = "TASK"
    APPROVAL = "APPROVAL"


class ActivityCursor(NamedTuple):
    occurred_at: datetime
    source_rank: int
    stable_source_id: str
    source_sequence: int


class TaskActivityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["TASK"]
    task_id: str = Field(min_length=1, max_length=128)


class ApprovalActivityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["APPROVAL"]
    approval_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)


class OperationActivityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["OPERATION"]
    status: ActivityStatus


ActivityContext = Annotated[
    TaskActivityContext | ApprovalActivityContext | OperationActivityContext,
    Field(discriminator="kind"),
]


class ActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["phase7-activity-v1"]
    activity_id: str = Field(min_length=1, max_length=256)
    occurred_at: datetime
    category: ActivityCategory
    event_code: ActivityEventCode
    status: ActivityStatus | None
    resource_type: ActivityResource | None
    resource_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    detail: str | None = Field(default=None, max_length=500)
    context: ActivityContext | None


class ActivityHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["phase7-activity-v1"]
    events: tuple[ActivityEvent, ...]
    next_cursor: str | None = Field(default=None, max_length=512)


@dataclass(frozen=True, slots=True)
class TaskDerivedDetail:
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ActionEventDetail:
    event_code: ActivityEventCode
    status: ActivityStatus | None


@dataclass(frozen=True, slots=True)
class ActivitySourceRow:
    source_kind: Literal["TASK_DERIVED", "ACTION_EVENT"]
    occurred_at: datetime
    stable_source_id: str
    source_sequence: int
    task_id: int | None
    approval_id: str | None
    action_event_code: ActivityEventCode | None
    task_creation_state: str | None
    task_failure_code: str | None
    approval_status: ActivityStatus | None
    operation_status: ActivityStatus | None
    typed_detail: TaskDerivedDetail | ActionEventDetail | None


def _validate_cursor(cursor: ActivityCursor) -> ActivityCursor:
    occurred_at, source_rank, stable_source_id, source_sequence = cursor
    if occurred_at.tzinfo is None or occurred_at.utcoffset() != timezone.utc.utcoffset(occurred_at):
        raise ValueError("cursor timestamp must be aware UTC")
    if source_rank not in {0, 1}:
        raise ValueError("cursor source rank is invalid")
    if not stable_source_id or len(stable_source_id) > 256:
        raise ValueError("cursor source identity is invalid")
    if source_sequence < 0 or source_sequence > 2_147_483_647:
        raise ValueError("cursor sequence is invalid")
    return ActivityCursor(
        occurred_at.astimezone(timezone.utc),
        source_rank,
        stable_source_id,
        source_sequence,
    )


def encode_activity_cursor(cursor: ActivityCursor) -> str:
    normalized = _validate_cursor(cursor)
    payload = {
        "occurred_at": normalized.occurred_at.isoformat().replace("+00:00", "Z"),
        "source_rank": normalized.source_rank,
        "source_sequence": normalized.source_sequence,
        "stable_source_id": normalized.stable_source_id,
        "version": 1,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def decode_activity_cursor(value: str) -> ActivityCursor:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode(), altchars=b"-_", validate=True)
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
    except (
        ValueError,
        UnicodeDecodeError,
        UnicodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("cursor is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {
        "occurred_at",
        "source_rank",
        "source_sequence",
        "stable_source_id",
        "version",
    }:
        raise ValueError("cursor is invalid")
    if payload["version"] != 1:
        raise ValueError("cursor version is unsupported")
    canonical = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    if base64.urlsafe_b64encode(canonical).decode().rstrip("=") != value:
        raise ValueError("cursor is not canonical")
    timestamp = payload["occurred_at"]
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise ValueError("cursor timestamp is invalid")
    try:
        occurred_at = datetime.fromisoformat(timestamp[:-1] + "+00:00")
        cursor = ActivityCursor(
            occurred_at,
            payload["source_rank"],
            payload["stable_source_id"],
            payload["source_sequence"],
        )
    except (TypeError, ValueError) as error:
        raise ValueError("cursor is invalid") from error
    return _validate_cursor(cursor)
