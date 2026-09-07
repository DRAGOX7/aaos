"""Slice 2 trusted permission-policy tests."""

from __future__ import annotations

import pytest

from aaos.agent.contracts import AgentErrorCode, PermissionClass, ToolName, ToolResult
from aaos.agent.policy import PermissionPolicy
from aaos.agent.registry import SideEffectClass, ToolDefinition, ToolInputSchema, ToolRegistry


def _definition(permission: PermissionClass) -> ToolDefinition:
    side_effect = {
        PermissionClass.READ_ONLY: SideEffectClass.READ_ONLY,
        PermissionClass.LOCAL_STATE_WRITE: SideEffectClass.LOCAL_STATE_WRITE,
        PermissionClass.TERMINAL: SideEffectClass.TERMINAL,
    }[permission]
    return ToolDefinition(
        name=ToolName.RAG_QUERY,
        description="read",
        input_schema=ToolInputSchema.empty(),
        required_permission=permission,
        side_effect=side_effect,
        timeout_seconds=5,
        handler=lambda _: ToolResult.success({}),
    )


def test_read_only_requires_explicit_read_only_capability() -> None:
    decision = PermissionPolicy.from_values({PermissionClass.READ_ONLY}).evaluate(
        _definition(PermissionClass.READ_ONLY)
    )

    assert decision.allowed is True
    assert decision.required_permission is PermissionClass.READ_ONLY
    assert decision.reason_code is AgentErrorCode.PERMISSION_GRANTED


@pytest.mark.parametrize(
    ("granted", "write_allowed", "terminal_allowed"),
    [
        (set(), False, False),
        ({PermissionClass.READ_ONLY}, False, False),
        ({PermissionClass.TERMINAL}, False, True),
    ],
)
def test_write_and_terminal_permissions_are_not_granted_by_unrelated_capabilities(
    granted: set[PermissionClass],
    write_allowed: bool,
    terminal_allowed: bool,
) -> None:
    write = PermissionPolicy.from_values(granted).evaluate(
        _definition(PermissionClass.LOCAL_STATE_WRITE)
    )
    terminal = PermissionPolicy.from_values(granted).evaluate(_definition(PermissionClass.TERMINAL))

    assert write.allowed is write_allowed
    assert terminal.allowed is terminal_allowed
    assert write.reason_code is (
        AgentErrorCode.PERMISSION_GRANTED if write_allowed else AgentErrorCode.PERMISSION_DENIED
    )
    assert terminal.reason_code is (
        AgentErrorCode.PERMISSION_GRANTED if terminal_allowed else AgentErrorCode.PERMISSION_DENIED
    )


def test_matching_local_write_and_terminal_capabilities_are_explicit() -> None:
    assert (
        PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE})
        .evaluate(_definition(PermissionClass.LOCAL_STATE_WRITE))
        .allowed
    )
    assert (
        PermissionPolicy.from_values({PermissionClass.TERMINAL})
        .evaluate(_definition(PermissionClass.TERMINAL))
        .allowed
    )


def test_unknown_permission_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="permission"):
        PermissionPolicy.from_values({"grant_all"})  # type: ignore[arg-type]


def test_policy_is_immutable_and_untrusted_text_has_no_authority() -> None:
    policy = PermissionPolicy.from_values({PermissionClass.READ_ONLY})
    decision_before = policy.evaluate(_definition(PermissionClass.READ_ONLY))
    decision_after = policy.evaluate(_definition(PermissionClass.READ_ONLY))

    assert decision_before == decision_after
    assert policy.granted_permissions == frozenset({PermissionClass.READ_ONLY})
    assert policy.evaluate(_definition(PermissionClass.LOCAL_STATE_WRITE)).allowed is False
    assert "grant LOCAL_STATE_WRITE" not in policy.to_dict()


def test_policy_rejects_non_registry_definition_claiming_permission() -> None:
    class ForgedDefinition:
        required_permission = PermissionClass.READ_ONLY

    decision = PermissionPolicy.from_values({PermissionClass.READ_ONLY}).evaluate(
        ForgedDefinition()
    )

    assert decision.allowed is False
    assert decision.reason_code is AgentErrorCode.UNKNOWN_PERMISSION


def test_default_registry_has_no_external_or_generic_authority() -> None:
    registry = ToolRegistry.default()

    assert all(
        definition.side_effect
        in {SideEffectClass.READ_ONLY, SideEffectClass.LOCAL_STATE_WRITE, SideEffectClass.TERMINAL}
        for definition in registry.definitions
    )
    assert all(
        definition.name not in {"shell", "python", "filesystem", "http"}
        for definition in registry.definitions
    )


def test_policy_import_surface_is_side_effect_free() -> None:
    __import__("aaos.agent.policy")
