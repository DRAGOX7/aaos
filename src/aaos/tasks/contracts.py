"""Closed trusted task contracts used by the Phase 7E application layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aaos.approval.contracts import (
        ActionId,
        ActionState,
        ActorIdentity,
        ApprovalState,
        OperationState,
    )

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a safe logical identifier")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{name} must be an aware UTC datetime")
    return value


class TaskKind(StrEnum):
    CALENDAR_CREATE_EVENT = "CALENDAR_CREATE_EVENT"


class TaskCreationState(StrEnum):
    CREATED = "CREATED"
    ACTION_BOUND = "ACTION_BOUND"
    FAILED = "FAILED"


class TaskFailureCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    PREPARATION_FAILED = "PREPARATION_FAILED"


class BrowserTaskState(StrEnum):
    PREPARING = "PREPARING"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    task_kind: TaskKind
    actor_identity: ActorIdentity
    idempotency_key: str
    request_fingerprint: str
    creation_state: TaskCreationState
    failure_code: TaskFailureCode | None
    action_id: ActionId | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        from aaos.approval.contracts import ActionId, ActorIdentity

        _id(self.task_id, "task_id")
        if not isinstance(self.task_kind, TaskKind):
            raise TypeError("task_kind must be a TaskKind")
        if not isinstance(self.actor_identity, ActorIdentity):
            raise TypeError("actor_identity must be an ActorIdentity")
        _id(self.idempotency_key, "idempotency_key")
        if not isinstance(self.request_fingerprint, str) or not _HASH.fullmatch(
            self.request_fingerprint
        ):
            raise ValueError("request_fingerprint must be a lowercase SHA-256 digest")
        if not isinstance(self.creation_state, TaskCreationState):
            raise TypeError("creation_state must be a TaskCreationState")
        if self.failure_code is not None and not isinstance(self.failure_code, TaskFailureCode):
            raise TypeError("failure_code must be a TaskFailureCode")
        if self.action_id is not None and not isinstance(self.action_id, ActionId):
            raise TypeError("action_id must be an ActionId")
        _utc(self.created_at, "created_at")
        _utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")


def project_task_state(
    creation_state: TaskCreationState,
    action_state: ActionState | None,
    approval_state: ApprovalState | None,
    operation_state: OperationState | None,
) -> BrowserTaskState:
    """Project authoritative Phase 6 state without duplicating it."""

    from aaos.approval.contracts import ActionState

    if not isinstance(creation_state, TaskCreationState):
        raise TypeError("creation_state must be a TaskCreationState")
    if creation_state is TaskCreationState.FAILED:
        return BrowserTaskState.PREPARATION_FAILED
    if action_state is None:
        return BrowserTaskState.PREPARING
    if action_state is ActionState.PREPARED:
        return BrowserTaskState.PREPARING
    if action_state is ActionState.AWAITING_APPROVAL:
        return BrowserTaskState.AWAITING_APPROVAL
    if action_state is ActionState.APPROVED:
        return BrowserTaskState.APPROVED
    if action_state is ActionState.CLAIMED:
        return BrowserTaskState.CLAIMED
    if action_state is ActionState.EXECUTING:
        return BrowserTaskState.EXECUTING
    if action_state in {ActionState.UNKNOWN_OUTCOME, ActionState.RECONCILIATION_PENDING}:
        return BrowserTaskState.UNKNOWN_OUTCOME
    if action_state is ActionState.CONFIRMED_SUCCESS:
        return BrowserTaskState.CONFIRMED_SUCCESS
    if action_state is ActionState.CONFIRMED_FAILURE:
        return BrowserTaskState.CONFIRMED_FAILURE
    if action_state is ActionState.REJECTED:
        return BrowserTaskState.REJECTED
    if action_state is ActionState.INVALIDATED:
        return BrowserTaskState.INVALIDATED
    if action_state is ActionState.EXPIRED:
        return BrowserTaskState.EXPIRED
    if action_state is ActionState.REVOKED:
        return BrowserTaskState.REVOKED
    raise ValueError("unsupported action state")


__all__ = [
    "BrowserTaskState",
    "TaskCreationState",
    "TaskFailureCode",
    "TaskKind",
    "TaskRecord",
    "project_task_state",
]
