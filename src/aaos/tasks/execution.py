"""Trusted Phase 7E composition over the frozen Phase 6 execution boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import ExternalAdapter, dispatch_claimed_operation
from aaos.approval.contracts import (
    AdapterOutcomeKind,
    ClaimIdentity,
    Phase6ActionDefinition,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import (
    claim_action,
    reconcile_operation,
    record_adapter_outcome,
)
from aaos.tasks.repository import TaskRepository
from aaos.web.contracts import TaskDetail


class TaskExecutionReader(Protocol):
    def get_task(self, task_id: str) -> TaskDetail: ...


class TaskExecutionCoordinator:
    """Run one trusted Phase 6 operation for an approved task.

    This is an internal composition point.  No browser route calls it and no
    branch performs a second adapter execution after an unknown outcome.
    """

    def __init__(
        self,
        task_repository: TaskRepository,
        approval_repository: ApprovalRepository,
        reader: TaskExecutionReader,
        *,
        adapter: ExternalAdapter,
        definition: Phase6ActionDefinition,
        capability_policy: PermissionPolicy,
    ) -> None:
        if not isinstance(task_repository, TaskRepository):
            raise TypeError("task_repository must be a TaskRepository")
        if not isinstance(approval_repository, ApprovalRepository):
            raise TypeError("approval_repository must be an ApprovalRepository")
        if not callable(getattr(reader, "get_task", None)):
            raise TypeError("reader must provide get_task")
        if not hasattr(adapter, "execute") or not hasattr(adapter, "reconcile"):
            raise TypeError("adapter must provide execute and reconcile")
        if not isinstance(definition, Phase6ActionDefinition):
            raise TypeError("definition must be a Phase6ActionDefinition")
        if not isinstance(capability_policy, PermissionPolicy):
            raise TypeError("capability_policy must be a PermissionPolicy")
        self._tasks = task_repository
        self._approvals = approval_repository
        self._reader = reader
        self._adapter = adapter
        self._definition = definition
        self._capability_policy = capability_policy

    def execute_approved_task(self, task_id: str, *, now: datetime) -> TaskDetail:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a nonempty string")
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(now)
        ):
            raise ValueError("now must be an aware UTC datetime")
        record = self._tasks.get(task_id)
        if record is None:
            raise ValueError("task was not found")
        if record.action_id is None:
            return self._reader.get_task(task_id)

        claim = claim_action(
            self._approvals,
            record.action_id,
            capability_policy=self._capability_policy,
            definition=self._definition,
            now=now,
            claim_identity=ClaimIdentity(f"phase7e-{task_id}"),
        )
        if not claim.claimed or claim.operation_id is None:
            return self._reader.get_task(task_id)

        dispatch = dispatch_claimed_operation(
            self._approvals,
            self._adapter,
            claim.operation_id,
            capability_policy=self._capability_policy,
            definition=self._definition,
            now=now,
        )
        outcome = dispatch.adapter_outcome
        if outcome is None:
            return self._reader.get_task(task_id)

        recorded = record_adapter_outcome(
            self._approvals,
            claim.operation_id,
            expected_revision=1,
            outcome=outcome,
        )
        if (
            recorded.operation_state.value == "UNKNOWN_OUTCOME"
            and outcome.kind is AdapterOutcomeKind.UNKNOWN
        ):
            reconcile_operation(
                self._approvals,
                self._adapter,
                claim.operation_id,
                expected_revision=2,
                now=now,
            )
        return self._reader.get_task(task_id)


__all__ = ["TaskExecutionCoordinator"]
