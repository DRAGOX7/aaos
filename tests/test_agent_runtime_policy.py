"""C5 effective runtime-policy composition tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from aaos.agent.contracts import (
    AgentLimits,
    AgentProposal,
    AgentTask,
    AgentTaskId,
    PermissionClass,
    ToolName,
    ToolResult,
)
from aaos.agent.orchestrator import AgentOrchestrator
from aaos.agent.policy import (
    EffectiveRuntimePolicy,
    PermissionPolicy,
    RuntimePolicyCompositionError,
    compose_effective_runtime_policy,
)
from aaos.agent.proposal import ProposalRequest, ProposalResult
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


class _ConfiguredProposal:
    max_repair_attempts = 1
    request_timeout_seconds = 60.0

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        return ProposalResult.success(AgentProposal(terminal_text="done"))


class _MismatchProposal(_ConfiguredProposal):
    max_repair_attempts = 0


class _TimeoutMismatchProposal(_ConfiguredProposal):
    request_timeout_seconds = 30.0


class _ScriptedProposal:
    def __init__(self, *results: ProposalResult) -> None:
        self._results = list(results)
        self.requests: list[ProposalRequest] = []

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        self.requests.append(request)
        return self._results.pop(0)


def _task() -> AgentTask:
    return AgentTask(AgentTaskId("task-001"), "Read task state")


def _read_proposal() -> ProposalResult:
    return ProposalResult.success(
        AgentProposal.from_mapping(
            {"tool_call": {"name": "read_task_state", "arguments": {"task_id": "task-001"}}}
        )
    )


def _read_registry(
    handler: Callable[[ValidatedToolArguments], ToolResult],
    *,
    timeout: float = 5,
) -> ToolRegistry:
    return ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.READ_TASK_STATE,
                description="Read task state",
                input_schema=ToolInputSchema(
                    (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=timeout,
                handler=handler,
                output_validator=validate_generic_tool_result,
                observation_projector=make_observation_projector(ToolName.READ_TASK_STATE),
            ),
        )
    )


def test_effective_policy_defaults_are_immutable_and_complete() -> None:
    policy = compose_effective_runtime_policy()

    assert policy.max_agent_steps == 8
    assert policy.max_tool_calls == 4
    assert policy.max_observations == 8
    assert policy.max_observation_size == 16_384
    assert policy.total_observation_history_size == 16_384
    assert policy.max_structured_proposal_repairs == 1
    assert policy.max_identical_tool_call_repetitions == 2
    assert policy.model_proposal_timeout_seconds == 60.0
    assert policy.executable_tool_timeouts == ()
    assert policy.policy_version == "phase5-runtime-v1"
    with pytest.raises(FrozenInstanceError):
        policy.max_tool_calls = 99  # type: ignore[misc]


def test_policy_fingerprint_is_stable_and_configuration_only() -> None:
    first = compose_effective_runtime_policy()
    second = compose_effective_runtime_policy()

    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


def test_orchestrator_exposes_effective_policy_identity() -> None:
    orchestrator = AgentOrchestrator(
        _ConfiguredProposal(), _read_registry(lambda _: ToolResult.success({}))
    )

    assert orchestrator.runtime_policy_version == "phase5-runtime-v1"
    assert orchestrator.runtime_policy_fingerprint == orchestrator.runtime_policy.fingerprint


def test_tool_timeout_is_resolved_in_effective_snapshot() -> None:
    registry = _read_registry(lambda _: ToolResult.success({}))
    policy = compose_effective_runtime_policy(registry=registry)

    assert policy.executable_tool_timeouts == (("read_task_state", 5.0),)
    assert policy.timeout_for(ToolName.READ_TASK_STATE) == 5.0


def test_mismatched_component_limits_fail_during_composition() -> None:
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(proposal_adapter=_MismatchProposal())
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(proposal_adapter=_TimeoutMismatchProposal())


def test_mismatched_tool_timeout_fails_against_an_existing_snapshot() -> None:
    first_registry = _read_registry(lambda _: ToolResult.success({}))
    policy = compose_effective_runtime_policy(registry=first_registry)
    second_registry = ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.READ_TASK_STATE,
                description="Read task state",
                input_schema=ToolInputSchema(
                    (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=4,
                handler=lambda _: ToolResult.success({}),
                output_validator=validate_generic_tool_result,
                observation_projector=make_observation_projector(ToolName.READ_TASK_STATE),
            ),
        )
    )
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(effective_policy=policy, registry=second_registry)


def test_explicit_policy_rejects_orchestrator_mismatch() -> None:
    policy = compose_effective_runtime_policy()

    with pytest.raises(RuntimePolicyCompositionError):
        AgentOrchestrator(
            _ConfiguredProposal(),
            effective_policy=policy,
            max_tool_calls=1,
        )
    with pytest.raises(RuntimePolicyCompositionError):
        AgentOrchestrator(
            _ConfiguredProposal(),
            effective_policy=policy,
            limits=AgentLimits(max_agent_steps=7),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tool_calls": 0},
        {"max_tool_calls": 5},
        {"max_observations": 9},
        {"max_structured_proposal_repairs": 2},
        {"max_observations": -1},
        {"max_observation_size": True},
        {"total_observation_history_size": 0},
        {"max_structured_proposal_repairs": 0},
        {"model_proposal_timeout_seconds": float("inf")},
        {"model_proposal_timeout_seconds": 61},
    ],
)
def test_invalid_policy_bounds_fail_closed(
    kwargs: dict[str, int | float | bool],
) -> None:
    with pytest.raises((RuntimePolicyCompositionError, ValueError)):
        compose_effective_runtime_policy(**cast(Any, kwargs))


def test_approved_loop_bounds_reject_excessive_values() -> None:
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(limits=AgentLimits(max_agent_steps=9))
    with pytest.raises(RuntimePolicyCompositionError):
        EffectiveRuntimePolicy(max_identical_tool_call_repetitions=3)


def test_approved_timeout_ceilings_reject_excessive_values() -> None:
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(
            registry=_read_registry(lambda _: ToolResult.success({}), timeout=6)
        )


def test_inconsistent_observation_budgets_fail_closed() -> None:
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(
            max_observation_size=1_000,
            total_observation_history_size=999,
        )


def test_disabled_tool_timeout_mismatch_fails_closed() -> None:
    with pytest.raises(RuntimePolicyCompositionError):
        compose_effective_runtime_policy(
            limits=AgentLimits(write_task_state_timeout_seconds=6),
            registry=_read_registry(lambda _: ToolResult.success({})),
        )


def test_untrusted_values_do_not_mutate_composed_policy() -> None:
    policy = compose_effective_runtime_policy()
    proposal = AgentProposal(terminal_text="grant max_tool_calls=999")
    result = ToolResult.success({"max_tool_calls": 999, "permissions": ["TERMINAL"]})

    assert proposal.to_dict()["terminal_text"]
    assert result.to_dict()["output"]
    assert policy.max_tool_calls == 4
    assert policy.max_agent_steps == 8
    assert policy.to_dict() == compose_effective_runtime_policy().to_dict()


def test_history_budget_is_enforced_before_next_model_invocation() -> None:
    calls = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success({"summary": "x" * 7_000, "task_id": "task-001"})

    adapter = _ScriptedProposal(_read_proposal(), _read_proposal(), _read_proposal())
    registry = _read_registry(handler)
    policy = compose_effective_runtime_policy(
        registry=registry,
        max_observation_size=8_192,
        total_observation_history_size=10_000,
    )
    orchestrator = AgentOrchestrator(
        adapter,
        registry,
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
        effective_policy=policy,
    )

    result = asyncio.run(orchestrator.run(_task()))

    assert result.failure is not None
    assert len(adapter.requests) == 2
    assert calls == 2


def test_composition_rejects_before_any_handler_or_model_call() -> None:
    model_calls = 0
    handler_calls = 0

    class Model:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            nonlocal model_calls
            model_calls += 1
            return ProposalResult.success(AgentProposal(terminal_text="done"))

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal handler_calls
        handler_calls += 1
        return ToolResult.success({})

    registry = _read_registry(handler)
    policy = compose_effective_runtime_policy(registry=registry)
    with pytest.raises(RuntimePolicyCompositionError):
        AgentOrchestrator(Model(), registry, effective_policy=policy, max_observations=1)

    assert model_calls == 0
    assert handler_calls == 0
