"""Concurrency coverage for browser approval decision commands."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

from fastapi.testclient import TestClient

from aaos.approval.contracts import (
    ApprovalId,
    Phase6ErrorCode,
    TrustedApproverContext,
)
from aaos.approval.service import Phase6ServiceError
from aaos.main import create_app
from aaos.web.contracts import (
    ApprovalDetail,
    ApprovalSummary,
    SafeActionSummary,
)
from aaos.web.contracts import (
    ApprovalState as ApprovalViewState,
)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
APPROVER = TrustedApproverContext("local-user", "local-context")


def _detail(approval_id: str) -> ApprovalDetail:
    action = SafeActionSummary(
        action_type="CREATE_EVENT",
        integration_label="Google Calendar",
        summary="Planning",
        start="2026-09-18T14:00:00Z",
        end="2026-09-18T15:00:00Z",
        timezone="UTC",
    )
    summary = ApprovalSummary(
        approval_id=approval_id,
        task_id="task-1",
        action=action,
        status=ApprovalViewState.APPROVED,
        issued_at=NOW,
        approval_expires_at=NOW.replace(hour=13),
        operation_state=None,
        can_approve=False,
        can_reject=False,
    )
    return ApprovalDetail(**summary.model_dump(), decided_at=NOW, decision_reason="USER_APPROVED")


class CompareAndSwapCommands:
    def __init__(self) -> None:
        self._lock = Lock()
        self._decided = False
        self.calls = 0

    def approve(
        self, approval_id: ApprovalId, *, approver: TrustedApproverContext, now: datetime
    ) -> ApprovalDetail:
        del approver, now
        with self._lock:
            self.calls += 1
            if self._decided:
                raise Phase6ServiceError(Phase6ErrorCode.INVALID_STATE_TRANSITION)
            self._decided = True
        return _detail(approval_id.value)

    def reject(
        self, approval_id: ApprovalId, *, approver: TrustedApproverContext, now: datetime
    ) -> ApprovalDetail:
        return self.approve(approval_id, approver=approver, now=now)


class Principal:
    def current(self) -> TrustedApproverContext:
        return APPROVER


def test_concurrent_decisions_have_one_authoritative_winner() -> None:
    commands = CompareAndSwapCommands()
    client = TestClient(
        create_app(
            task_commands=commands,
            principal_provider=Principal(),
        ),
        raise_server_exceptions=False,
    )
    headers = {
        "host": "127.0.0.1:8000",
        "origin": "http://127.0.0.1:3000",
        "content-type": "application/json",
    }

    def decide(path: str) -> int:
        return client.post(path, headers=headers, json={}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                decide,
                (
                    "/api/v1/approvals/approval-1/approve",
                    "/api/v1/approvals/approval-1/reject",
                ),
            )
        )

    assert sorted(statuses) == [200, 409]
    assert commands.calls == 2
