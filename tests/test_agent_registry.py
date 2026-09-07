"""Slice 2 trusted allowlist and handler-boundary tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan
from typing import Callable

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentProposal,
    PermissionClass,
    ToolArguments,
    ToolName,
    ToolResult,
)
from aaos.agent.policy import PermissionPolicy
from aaos.agent.registry import (
    SideEffectClass,
    ToolDefinition,
    ToolField,
    ToolInputSchema,
    ToolRegistry,
    ToolValueType,
    ValidatedToolArguments,
)


def _schema(*fields: ToolField) -> ToolInputSchema:
    return ToolInputSchema(tuple(fields))


def _definition(
    handler: Callable[[ValidatedToolArguments], ToolResult] | None,
    *,
    name: ToolName = ToolName.RAG_QUERY,
    permission: PermissionClass = PermissionClass.READ_ONLY,
    schema: ToolInputSchema | None = None,
    timeout: float = 120,
) -> ToolDefinition:
    side_effect = {
        PermissionClass.READ_ONLY: SideEffectClass.READ_ONLY,
        PermissionClass.LOCAL_STATE_WRITE: SideEffectClass.LOCAL_STATE_WRITE,
        PermissionClass.TERMINAL: SideEffectClass.TERMINAL,
    }[permission]
    return ToolDefinition(
        name=name,
        description="A bounded test tool",
        input_schema=schema
        or ToolInputSchema((ToolField("question", ToolValueType.STRING),), "rag_query"),
        required_permission=permission,
        side_effect=side_effect,
        timeout_seconds=timeout,
        handler=handler,
    )


def _proposal(arguments: dict[str, object]) -> AgentProposal:
    return AgentProposal.from_mapping({"tool_call": {"name": "rag_query", "arguments": arguments}})


def test_valid_call_crosses_explicit_validated_invocation_boundary() -> None:
    calls: list[ValidatedToolArguments] = []

    def handler(arguments: ValidatedToolArguments) -> ToolResult:
        calls.append(arguments)
        return ToolResult.success({"answer": arguments.to_mapping()["question"]})

    registry = ToolRegistry((_definition(handler),))
    result = registry.execute(
        _proposal({"question": "What is due?"}),
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is True
    assert calls == [calls[0]]
    assert calls[0].to_mapping() == {"question": "What is due?"}
    assert calls[0].schema_name == "rag_query"
    assert ToolArguments.from_mapping({"question": "q"}).validated is False


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"question": "q", "unexpected": True},
        {"question": 3},
        {"question": None},
    ],
)
def test_invalid_arguments_never_reach_handler(arguments: dict[str, object]) -> None:
    call_count = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal call_count
        call_count += 1
        return ToolResult.success({})

    registry = ToolRegistry((_definition(handler),))
    result = registry.execute(
        _proposal(arguments), PermissionPolicy.from_values({PermissionClass.READ_ONLY})
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_TOOL_ARGUMENTS
    assert call_count == 0


def test_denied_permission_never_reaches_handler() -> None:
    call_count = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal call_count
        call_count += 1
        return ToolResult.success({})

    registry = ToolRegistry((_definition(handler),))
    result = registry.execute(_proposal({"question": "q"}), PermissionPolicy())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.PERMISSION_DENIED
    assert call_count == 0


def test_unknown_tool_payload_is_rejected_without_dynamic_discovery() -> None:
    call_count = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal call_count
        call_count += 1
        return ToolResult.success({})

    registry = ToolRegistry((_definition(handler),))
    result = registry.execute_payload(
        {"tool_call": {"name": "unknown_tool", "arguments": {}}},
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.UNKNOWN_TOOL
    assert call_count == 0


def test_duplicate_registration_is_a_configuration_error_and_never_replaces() -> None:
    def first(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({"which": "first"})

    def second(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({"which": "second"})

    with pytest.raises(ValueError, match="DUPLICATE_TOOL_REGISTRATION"):
        ToolRegistry((_definition(first), _definition(second)))


def test_dynamic_registration_is_forbidden_and_registry_remains_unchanged() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({})

    registry = ToolRegistry((_definition(handler),))

    with pytest.raises(ValueError, match="DYNAMIC_TOOL_REGISTRATION"):
        registry.register(_definition(handler, name=ToolName.READ_TASK_STATE))
    assert registry.names == (ToolName.RAG_QUERY,)


def test_tool_definition_is_immutable_and_handler_is_trusted_only() -> None:
    definition = _definition(lambda _: ToolResult.success({}))

    with pytest.raises(FrozenInstanceError):
        definition.handler = lambda _: ToolResult.success({})  # type: ignore[misc]
    assert definition.to_metadata()["name"] == "rag_query"
    assert "handler" not in definition.to_metadata()


@pytest.mark.parametrize("timeout", [0, -1, inf, nan, None])
def test_tool_definition_requires_finite_positive_timeout(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        _definition(lambda _: ToolResult.success({}), timeout=timeout)  # type: ignore[arg-type]


def test_handler_exception_is_sanitized_and_never_exposes_raw_exception() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        raise RuntimeError("secret internal detail")

    result = ToolRegistry((_definition(handler),)).execute(
        _proposal({"question": "q"}),
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.HANDLER_EXECUTION_FAILED
    assert "secret" not in result.error.message


def test_model_cannot_inject_permission_state_or_executable_argument_shape() -> None:
    call_count = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal call_count
        call_count += 1
        return ToolResult.success({})

    registry = ToolRegistry((_definition(handler),))
    result = registry.execute_payload(
        {
            "permission": "LOCAL_STATE_WRITE",
            "state": "COMPLETED",
            "tool_call": {
                "name": "rag_query",
                "arguments": {"question": "powershell ../../secret"},
            },
        },
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_PROPOSAL
    assert call_count == 0


def test_safe_identifier_field_rejects_path_like_argument() -> None:
    definition = _definition(
        lambda _: ToolResult.success({}),
        schema=_schema(ToolField("task_id", ToolValueType.SAFE_IDENTIFIER)),
    )
    registry = ToolRegistry((definition,))
    proposal = AgentProposal.from_mapping(
        {"tool_call": {"name": "rag_query", "arguments": {"task_id": "../../secret"}}}
    )

    result = registry.execute(proposal, PermissionPolicy.from_values({PermissionClass.READ_ONLY}))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_TOOL_ARGUMENTS


def test_default_registry_is_metadata_only_for_initial_inventory() -> None:
    registry = ToolRegistry.default()

    assert registry.names == (
        ToolName.RAG_QUERY,
        ToolName.READ_TASK_STATE,
        ToolName.WRITE_TASK_STATE,
        ToolName.COMPLETE_TASK,
    )
    write_definition = registry.get(ToolName.WRITE_TASK_STATE)
    assert write_definition.required_permission is PermissionClass.LOCAL_STATE_WRITE
    assert write_definition.handler is None

    result = registry.execute(
        AgentProposal.from_mapping(
            {
                "tool_call": {
                    "name": "write_task_state",
                    "arguments": {"task_id": "task-1", "note": "safe note"},
                }
            }
        ),
        PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.TOOL_NOT_EXECUTABLE


def test_write_task_state_cannot_receive_a_handler_in_slice_two() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({})

    with pytest.raises(ValueError, match="write_task_state"):
        _definition(
            handler, name=ToolName.WRITE_TASK_STATE, permission=PermissionClass.LOCAL_STATE_WRITE
        )


def test_registry_import_surface_is_side_effect_free() -> None:
    __import__("aaos.agent.registry")
