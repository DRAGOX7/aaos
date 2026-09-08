"""C6 trusted input/output tool-contract boundary tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentProposal,
    PermissionClass,
    ToolName,
    ToolObservation,
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
    make_observation_projector,
    validate_generic_tool_result,
)


def _proposal(arguments: dict[str, object]) -> AgentProposal:
    return AgentProposal.from_mapping(
        {"tool_call": {"name": ToolName.READ_TASK_STATE.value, "arguments": arguments}}
    )


def _definition(
    handler: Callable[[ValidatedToolArguments], ToolResult] | None,
    *,
    output_validator: object = ...,
    observation_projector: object = ...,
) -> ToolDefinition:
    values: dict[str, object] = {
        "name": ToolName.READ_TASK_STATE,
        "description": "Read bounded task state",
        "input_schema": ToolInputSchema(
            (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
        ),
        "required_permission": PermissionClass.READ_ONLY,
        "side_effect": SideEffectClass.READ_ONLY,
        "timeout_seconds": 5,
        "handler": handler,
        "output_validator": validate_generic_tool_result,
        "observation_projector": make_observation_projector(ToolName.READ_TASK_STATE),
    }
    if output_validator is not ...:
        values["output_validator"] = output_validator
    if observation_projector is not ...:
        values["observation_projector"] = observation_projector
    return ToolDefinition(**values)  # type: ignore[arg-type]


def _policy() -> PermissionPolicy:
    return PermissionPolicy.from_values({PermissionClass.READ_ONLY})


def test_executable_definition_exposes_explicit_trusted_contracts() -> None:
    definition = _definition(lambda _: ToolResult.success({"state": "ready"}))

    assert callable(definition.input_validator)
    assert callable(definition.output_validator)
    assert callable(definition.observation_projector)
    assert definition.contract_version == "phase5-tool-contract-v1"
    metadata = definition.to_metadata()
    assert metadata["contract_version"] == definition.contract_version
    assert "handler" not in metadata
    assert "output_validator" not in metadata
    assert "observation_projector" not in metadata


def test_model_metadata_is_derived_from_the_bound_input_contract() -> None:
    definition = _definition(lambda _: ToolResult.success({}))
    metadata = definition.to_metadata()

    assert metadata["description"] == definition.description
    assert metadata["input_schema"] == definition.input_schema.to_dict()
    assert metadata["output_contract"] == {
        "name": definition.result_schema,
        "version": definition.contract_version,
    }


def test_malformed_handler_output_fails_before_observation() -> None:
    calls = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success("not a result object")

    result = ToolRegistry((_definition(handler),)).execute(
        _proposal({"task_id": "task-1"}), _policy()
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.HANDLER_EXECUTION_FAILED
    assert calls == 1


def test_output_validator_rejects_a_malformed_success_mapping() -> None:
    def reject(_: ToolResult) -> ToolResult:
        raise ValueError("invalid output")

    result = ToolRegistry(
        (_definition(lambda _: ToolResult.success({}), output_validator=reject),)
    ).execute(_proposal({"task_id": "task-1"}), _policy())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.HANDLER_EXECUTION_FAILED


def test_incomplete_executable_contract_is_rejected_at_registry_composition() -> None:
    with pytest.raises(ValueError, match="output_validator"):
        ToolRegistry(
            (
                _definition(
                    lambda _: ToolResult.success({}),
                    output_validator=None,
                ),
            )
        )


def test_omitted_executable_contract_is_rejected_at_registry_composition() -> None:
    definition = ToolDefinition(
        name=ToolName.READ_TASK_STATE,
        description="Read bounded task state",
        input_schema=ToolInputSchema(
            (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
        ),
        required_permission=PermissionClass.READ_ONLY,
        side_effect=SideEffectClass.READ_ONLY,
        timeout_seconds=5,
        handler=lambda _: ToolResult.success({}),
    )
    with pytest.raises(ValueError, match="output_validator"):
        ToolRegistry((definition,))


def test_incomplete_observation_projection_is_rejected_at_registry_composition() -> None:
    with pytest.raises(ValueError, match="observation_projector"):
        ToolRegistry(
            (
                _definition(
                    lambda _: ToolResult.success({}),
                    observation_projector=None,
                ),
            )
        )


def test_projection_is_trusted_and_must_return_a_tool_observation() -> None:
    def invalid_projection(_: ToolResult, __: int) -> object:
        return {"status": "OK"}

    definition = _definition(
        lambda _: ToolResult.success({}), observation_projector=invalid_projection
    )
    registry = ToolRegistry((definition,))

    result = registry.execute(_proposal({"task_id": "task-1"}), _policy())
    assert result.ok is True

    with pytest.raises(ValueError, match="ToolObservation"):
        definition.project_observation(result, max_size=1024)


def test_unexpected_projector_exception_is_wrapped_at_observation_boundary() -> None:
    def exploding_projector(_: ToolResult, __: int) -> ToolObservation:
        raise RuntimeError("private projector failure")

    definition = _definition(
        lambda _: ToolResult.success({}), observation_projector=exploding_projector
    )
    result = ToolRegistry((definition,)).execute(_proposal({"task_id": "task-1"}), _policy())

    assert result.ok is True
    with pytest.raises(ValueError, match="observation projector failed safely"):
        definition.project_observation(result, max_size=1024)


def test_disabled_tools_retain_contract_metadata_without_execution_authority() -> None:
    definition = ToolRegistry.default().get(ToolName.WRITE_TASK_STATE)

    assert definition.handler is None
    assert callable(definition.output_validator)
    assert callable(definition.observation_projector)
    result = ToolRegistry.default().execute(
        AgentProposal.from_mapping(
            {
                "tool_call": {
                    "name": ToolName.WRITE_TASK_STATE.value,
                    "arguments": {"task_id": "task-1", "note": "safe"},
                }
            }
        ),
        PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE}),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.TOOL_NOT_EXECUTABLE


def test_projection_preserves_the_c4_observation_boundary() -> None:
    definition = _definition(lambda _: ToolResult.success({"summary": "safe"}))
    result = ToolRegistry((definition,)).execute(_proposal({"task_id": "task-1"}), _policy())

    assert result.ok is True
    observation = definition.project_observation(result, max_size=1024)
    assert isinstance(observation, ToolObservation)
    assert observation.status == "OK"
    assert dict(observation.fields)["summary"] == "safe"


def test_result_schema_label_cannot_bypass_output_validation() -> None:
    with pytest.raises(ValueError, match="output_validator"):
        ToolRegistry(
            (
                _definition(
                    lambda _: ToolResult.success({}),
                    output_validator=None,
                ),
            )
        )
