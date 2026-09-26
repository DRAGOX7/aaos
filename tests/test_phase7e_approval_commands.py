"""RED tests for the Phase 7E browser approval decision boundary."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
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


def _approval_detail(approval_id: str = "approval-1") -> ApprovalDetail:
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
    return ApprovalDetail(
        **summary.model_dump(),
        decided_at=NOW,
        decision_reason="USER_APPROVED",
    )


class RecordingCommands:
    def __init__(self) -> None:
        self.approved: list[tuple[ApprovalId, TrustedApproverContext]] = []
        self.rejected: list[tuple[ApprovalId, TrustedApproverContext]] = []

    def approve(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        assert now.tzinfo is not None
        self.approved.append((approval_id, approver))
        return _approval_detail(approval_id.value)

    def reject(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        assert now.tzinfo is not None
        self.rejected.append((approval_id, approver))
        return _approval_detail(approval_id.value)


class RecordingPrincipal:
    def current(self) -> TrustedApproverContext:
        return APPROVER


class FailingCommands(RecordingCommands):
    def __init__(self, code: Phase6ErrorCode) -> None:
        super().__init__()
        self.code = code

    def approve(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        del approval_id, approver, now
        raise Phase6ServiceError(self.code)

    def reject(
        self,
        approval_id: ApprovalId,
        *,
        approver: TrustedApproverContext,
        now: datetime,
    ) -> ApprovalDetail:
        del approval_id, approver, now
        raise Phase6ServiceError(self.code)


def _client(commands: RecordingCommands) -> TestClient:
    return TestClient(
        create_app(
            task_commands=commands,
            principal_provider=RecordingPrincipal(),
        ),
        raise_server_exceptions=False,
    )


def _headers() -> dict[str, str]:
    return {
        "host": "127.0.0.1:8000",
        "origin": "http://127.0.0.1:3000",
        "content-type": "application/json",
    }


def test_approve_route_uses_server_approver_and_empty_json_body() -> None:
    commands = RecordingCommands()
    response = _client(commands).post(
        "/api/v1/approvals/approval-1/approve",
        headers=_headers(),
        json={},
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] == "approval-1"
    assert commands.approved == [(ApprovalId("approval-1"), APPROVER)]


def test_reject_route_uses_server_approver_and_never_dispatches() -> None:
    commands = RecordingCommands()
    response = _client(commands).post(
        "/api/v1/approvals/approval-1/reject",
        headers=_headers(),
        json={},
    )

    assert response.status_code == 200
    assert commands.rejected == [(ApprovalId("approval-1"), APPROVER)]


def test_approval_route_rejects_authority_fields_and_foreign_origin() -> None:
    commands = RecordingCommands()
    client = _client(commands)

    authority = client.post(
        "/api/v1/approvals/approval-1/approve",
        headers=_headers(),
        json={"approver_identity": "attacker"},
    )
    foreign = client.post(
        "/api/v1/approvals/approval-1/approve",
        headers={**_headers(), "origin": "https://attacker.invalid"},
        json={},
    )

    assert authority.status_code == 422
    assert foreign.status_code == 403
    assert commands.approved == []


@pytest.mark.parametrize(
    ("code", "status", "response_code"),
    [
        (Phase6ErrorCode.APPROVAL_NOT_FOUND, 404, "APPROVAL_NOT_FOUND"),
        (Phase6ErrorCode.APPROVAL_EXPIRED, 409, "APPROVAL_NOT_ACTIONABLE"),
        (Phase6ErrorCode.INVALID_STATE_TRANSITION, 409, "APPROVAL_NOT_ACTIONABLE"),
    ],
)
def test_terminal_or_missing_decisions_are_bounded(
    code: Phase6ErrorCode,
    status: int,
    response_code: str,
) -> None:
    response = _client(FailingCommands(code)).post(
        "/api/v1/approvals/approval-1/approve",
        headers=_headers(),
        json={},
    )

    assert response.status_code == status
    assert response.json()["error"]["code"] == response_code
