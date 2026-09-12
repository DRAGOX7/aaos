from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aaos.agent.contracts import PermissionClass
from aaos.approval.contracts import (
    ActionId,
    ActionType,
    ActorIdentity,
    ApprovalId,
    ApprovalRecord,
    ApprovalState,
    CanonicalArguments,
    ExecutableAction,
    OperationId,
    OperationRecord,
    OperationState,
    PreparedActionBinding,
    RemotePrecondition,
    TargetIdentity,
    TrustedApproverContext,
    build_prepared_action,
    canonicalize_arguments,
    compute_approval_binding_sha256,
    compute_prepared_action_sha256,
    project_executable_action,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


def utc_after(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


def make_target(value: str = "calendar-primary") -> TargetIdentity:
    return TargetIdentity(provider="fake-calendar", account="student", resource=value)


def make_prepared_action_binding(*, action_expires_at: datetime) -> PreparedActionBinding:
    arguments = canonicalize_arguments({"summary": "Submit assignment", "slots": [1, 2]})
    return PreparedActionBinding(
        action_id=ActionId("act-001"),
        operation_id=OperationId("op-001"),
        actor_identity=ActorIdentity("user-001"),
        target_identity=make_target(),
        action_type=ActionType("fake_calendar_create"),
        canonical_arguments=arguments.bytes,
        arguments_sha256=arguments.sha256,
        tool_name="fake_calendar_create",
        tool_schema_version="v1",
        tool_definition_fingerprint="a" * 64,
        policy_version="v1",
        policy_fingerprint="b" * 64,
        remote_precondition=RemotePrecondition(
            provider="fake-calendar", resource="calendar-primary", version="v1", etag="etag-001"
        ),
        prepared_at=NOW,
        action_expires_at=action_expires_at,
    )


def make_prepared_action():
    return build_prepared_action(make_prepared_action_binding(action_expires_at=utc_after(300)))


def make_approval(*, approval_expires_at: datetime, action_expires_at: datetime) -> ApprovalRecord:
    prepared = build_prepared_action(
        make_prepared_action_binding(action_expires_at=action_expires_at)
    )
    approver = TrustedApproverContext("approver-001", "session-001")
    return ApprovalRecord(
        approval_id=ApprovalId("approval-001"),
        action_id=prepared.action_id,
        operation_id=prepared.operation_id,
        approver=approver,
        prepared_action_sha256=prepared.prepared_action_sha256,
        approval_binding_sha256=compute_approval_binding_sha256(
            prepared.prepared_action_sha256, approver, approval_expires_at
        ),
        issued_at=NOW,
        approval_expires_at=approval_expires_at,
        action_expires_at=action_expires_at,
        status=ApprovalState.PENDING,
    )


def test_prepared_action_digest_covers_all_approval_bound_fields() -> None:
    first_binding = make_prepared_action_binding(action_expires_at=utc_after(300))
    changed_binding = replace(first_binding, target_identity=make_target("other"))

    assert compute_prepared_action_sha256(first_binding) != compute_prepared_action_sha256(
        changed_binding
    )
    assert build_prepared_action(
        first_binding
    ).prepared_action_sha256 == compute_prepared_action_sha256(first_binding)


def test_prepared_digest_intentionally_excludes_audit_timestamp() -> None:
    first_binding = make_prepared_action_binding(action_expires_at=utc_after(300))
    later_audit = replace(first_binding, prepared_at=utc_after(1))

    assert compute_prepared_action_sha256(first_binding) == compute_prepared_action_sha256(
        later_audit
    )


def test_canonical_arguments_are_compact_sorted_json_and_immutable() -> None:
    arguments = canonicalize_arguments({"z": [True, None], "a": "value"})

    assert arguments.bytes == b'{"a":"value","z":[true,null]}'
    assert arguments.sha256 == __import__("hashlib").sha256(arguments.bytes).hexdigest()
    with pytest.raises((AttributeError, TypeError)):
        arguments.bytes = b"changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        CanonicalArguments(b"{}", "0" * 64)


def test_approval_expiry_cannot_exceed_action_expiry() -> None:
    with pytest.raises(ValueError, match="approval_expires_at"):
        make_approval(approval_expires_at=utc_after(600), action_expires_at=utc_after(300))


def test_executable_projection_has_only_least_authority_fields() -> None:
    projected = project_executable_action(make_prepared_action())

    assert set(projected.to_dict()) == {
        "target_identity",
        "action_type",
        "canonical_arguments",
        "operation_id",
        "remote_precondition",
    }


def test_rejects_invalid_json_values_and_unknown_deserialization_fields() -> None:
    with pytest.raises((TypeError, ValueError)):
        canonicalize_arguments({"nan": float("nan")})
    with pytest.raises(ValueError, match="unknown"):
        ActionId.from_dict({"value": "act-001", "unexpected": "field"})


def test_approval_binding_changes_for_approver_context_or_deadline() -> None:
    prepared = make_prepared_action()
    first = TrustedApproverContext("approver-001", "session-001")
    different_context = TrustedApproverContext("approver-001", "session-002")

    assert compute_approval_binding_sha256(
        prepared.prepared_action_sha256, first, utc_after(200)
    ) != compute_approval_binding_sha256(
        prepared.prepared_action_sha256, different_context, utc_after(200)
    )
    assert compute_approval_binding_sha256(
        prepared.prepared_action_sha256, first, utc_after(200)
    ) != compute_approval_binding_sha256(prepared.prepared_action_sha256, first, utc_after(201))


def test_action_definition_is_static_and_validates_exact_canonical_arguments() -> None:
    from aaos.approval.contracts import Phase6ActionDefinition

    definition = Phase6ActionDefinition(
        action_type=ActionType("fake_calendar_create"),
        tool_name="fake_calendar_create",
        tool_schema_version="v1",
        tool_definition_fingerprint="a" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="v1",
        policy_fingerprint="b" * 64,
        timeout_seconds=30,
        argument_validator=lambda values: values,
    )
    assert (
        definition.validate_arguments({"title": "office hours"}).bytes
        == b'{"title":"office hours"}'
    )


def test_approval_lifecycle_fields_are_mutable_but_binding_fields_are_not() -> None:
    approval = make_approval(approval_expires_at=utc_after(200), action_expires_at=utc_after(300))

    approval.status = ApprovalState.APPROVED
    approval.decided_at = NOW
    assert approval.status is ApprovalState.APPROVED
    with pytest.raises(AttributeError):
        approval.action_id = ActionId("act-other")


def test_records_and_executable_action_reject_untrusted_runtime_values() -> None:
    prepared = make_prepared_action()
    with pytest.raises(TypeError):
        OperationRecord(OperationId("op-001"), ActionId("act-001"), "CLAIMED")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        OperationRecord(
            OperationId("op-001"),
            ActionId("act-001"),
            OperationState.CLAIMED,
            outcome="UNKNOWN_OUTCOME",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        __import__("aaos.approval.contracts", fromlist=["ActionRecord"]).ActionRecord(
            prepared,
            "PREPARED",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        __import__("aaos.approval.contracts", fromlist=["ApprovalConsumption"]).ApprovalConsumption(
            ApprovalId("approval-001"),
            ActionId("act-001"),
            OperationId("op-001"),
            "worker",
            NOW,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        ExecutableAction(
            make_target(),
            ActionType("fake_calendar_create"),
            "{}",
            OperationId("op-001"),
            prepared.remote_precondition,  # type: ignore[arg-type]
        )


def test_action_definition_validator_is_trusted_only_and_not_serialized() -> None:
    from aaos.approval.contracts import Phase6ActionDefinition

    definition = Phase6ActionDefinition(
        ActionType("fake_calendar_create"),
        "fake_calendar_create",
        "v1",
        "a" * 64,
        PermissionClass.LOCAL_STATE_WRITE,
        "v1",
        "b" * 64,
        30,
        lambda values: values,
    )
    assert definition.__dataclass_fields__["argument_validator"].repr is False
    assert definition.__dataclass_fields__["argument_validator"].compare is False
