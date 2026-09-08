"""C3 timeout, cancellation, and late-result lifecycle tests."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentLimits,
    AgentProposal,
    AgentState,
    AgentTask,
    AgentTaskId,
    AgentTerminalResult,
    ExecutionOutcome,
    PermissionClass,
    ToolError,
    ToolExecutionLifecycle,
    ToolExecutionLifecycleState,
    ToolName,
    ToolResult,
)
from aaos.agent.orchestrator import AgentOrchestrator
from aaos.agent.policy import PermissionPolicy
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


class ScriptedAdapter:
    def __init__(self, *proposals: AgentProposal) -> None:
        self.proposals = list(proposals)
        self.requests: list[ProposalRequest] = []

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        self.requests.append(request)
        return ProposalResult.success(self.proposals.pop(0))


def _task() -> AgentTask:
    return AgentTask(AgentTaskId("c3-task"), "Run the controlled operation")


def _tool() -> AgentProposal:
    return AgentProposal.from_mapping(
        {
            "tool_call": {
                "name": ToolName.READ_TASK_STATE.value,
                "arguments": {"task_id": "c3-task"},
            }
        }
    )


def _terminal() -> AgentProposal:
    return AgentProposal(terminal_text="done")


def _registry(
    handler: Callable[[ValidatedToolArguments], ToolResult], timeout: float
) -> ToolRegistry:
    return ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.READ_TASK_STATE,
                description="test operation",
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


def _orchestrator(adapter: ScriptedAdapter, registry: ToolRegistry) -> AgentOrchestrator:
    return AgentOrchestrator(
        adapter,
        registry,
        PermissionPolicy.from_values({PermissionClass.READ_ONLY, PermissionClass.TERMINAL}),
    )


def test_handler_success_before_deadline_is_known_and_terminated() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({"state": "ready"})

    adapter = ScriptedAdapter(_tool(), _terminal())
    result = asyncio.run(_orchestrator(adapter, _registry(handler, 1)).run(_task()))

    assert result.state is AgentState.COMPLETED
    assert result.tool_execution_lifecycle is not None
    lifecycle = result.tool_execution_lifecycle
    assert lifecycle.state is ToolExecutionLifecycleState.COMPLETED
    assert lifecycle.execution_outcome is ExecutionOutcome.KNOWN_SUCCESS
    assert lifecycle.execution_outcome_known is True
    assert lifecycle.operation_terminated is True
    assert lifecycle.request_deadline_exceeded is False


def test_handler_failure_before_deadline_is_known_and_terminated() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.failure(ToolError(AgentErrorCode.TOOL_FAILED, "safe failure"))

    adapter = ScriptedAdapter(_tool())
    result = asyncio.run(_orchestrator(adapter, _registry(handler, 1)).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.TOOL_FAILED
    assert result.tool_execution_lifecycle is not None
    lifecycle = result.tool_execution_lifecycle
    assert lifecycle.state is ToolExecutionLifecycleState.FAILED
    assert lifecycle.execution_outcome is ExecutionOutcome.KNOWN_FAILURE
    assert lifecycle.execution_outcome_known is True
    assert lifecycle.operation_terminated is True


@pytest.mark.parametrize("late_failure", [False, True])
def test_deadline_is_not_reported_as_termination_and_late_result_is_ignored(
    late_failure: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    handler_calls = 0
    late_results: list[str] = []

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal handler_calls
        handler_calls += 1
        started.set()
        release.wait(2)
        finished.set()
        if late_failure:
            late_results.append("failed")
            raise RuntimeError("late backend failure")
        late_results.append("succeeded")
        return ToolResult.success({"late": True})

    adapter = ScriptedAdapter(_tool(), _terminal())
    orchestrator = _orchestrator(adapter, _registry(handler, 0.02))

    async def scenario() -> tuple[AgentTerminalResult, int, int]:
        run_task = asyncio.create_task(orchestrator.run(_task()))
        assert await asyncio.to_thread(started.wait, 1) is True
        result = await run_task
        assert result.failure is not None
        assert result.failure.code is AgentErrorCode.TOOL_TIMEOUT
        release.set()
        assert await asyncio.to_thread(finished.wait, 1) is True
        await asyncio.sleep(0.02)
        return result, len(adapter.requests), handler_calls

    result, proposal_calls, observed_handler_calls = asyncio.run(scenario())

    assert result.state is AgentState.FAILED
    assert result.tool_execution_lifecycle is not None
    lifecycle = result.tool_execution_lifecycle
    assert lifecycle.state is ToolExecutionLifecycleState.DEADLINE_EXCEEDED
    assert lifecycle.request_deadline_exceeded is True
    assert lifecycle.cancellation_requested is False
    assert lifecycle.operation_terminated is not True
    assert lifecycle.execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert lifecycle.execution_outcome_known is False
    assert lifecycle.resource_cleanup_complete is not True
    assert proposal_calls == 1
    assert observed_handler_calls == 1
    assert len(late_results) == 1


@pytest.mark.parametrize("use_internal_cancel", [False, True])
def test_cancellation_request_is_not_claimed_as_operation_termination(
    use_internal_cancel: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handler(_: ValidatedToolArguments) -> ToolResult:
        started.set()
        release.wait(2)
        finished.set()
        return ToolResult.success({"late": True})

    adapter = ScriptedAdapter(_tool(), _terminal())
    orchestrator = _orchestrator(adapter, _registry(handler, 1))
    cancel_event = asyncio.Event()

    async def scenario() -> AgentTerminalResult:
        run_task = asyncio.create_task(orchestrator.run(_task(), cancel_event))
        assert await asyncio.to_thread(started.wait, 1) is True
        if use_internal_cancel:
            orchestrator.cancel()
        else:
            cancel_event.set()
        result = await run_task
        release.set()
        assert await asyncio.to_thread(finished.wait, 1) is True
        return result

    result = asyncio.run(scenario())

    assert result.state is AgentState.CANCELLED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.TASK_CANCELLED
    assert result.tool_execution_lifecycle is not None
    lifecycle = result.tool_execution_lifecycle
    assert lifecycle.state is ToolExecutionLifecycleState.CANCELLATION_REQUESTED
    assert lifecycle.cancellation_requested is True
    assert lifecycle.operation_terminated is not True
    assert lifecycle.execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME


def test_new_run_is_rejected_while_timed_out_operation_is_still_owned() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handler(_: ValidatedToolArguments) -> ToolResult:
        started.set()
        release.wait(2)
        finished.set()
        return ToolResult.success({"late": True})

    adapter = ScriptedAdapter(_tool(), _terminal())
    orchestrator = _orchestrator(adapter, _registry(handler, 0.02))

    async def scenario() -> tuple[AgentTerminalResult, AgentTerminalResult]:
        first_task = asyncio.create_task(orchestrator.run(_task()))
        assert await asyncio.to_thread(started.wait, 1) is True
        first_result = await first_task
        second_result = await orchestrator.run(_task())
        release.set()
        assert await asyncio.to_thread(finished.wait, 1) is True
        return first_result, second_result

    first_result, second_result = asyncio.run(scenario())

    assert first_result.failure is not None
    assert first_result.failure.code is AgentErrorCode.TOOL_TIMEOUT
    assert second_result.failure is not None
    assert second_result.failure.code is AgentErrorCode.TOOL_EXECUTION_IN_PROGRESS
    assert len(adapter.requests) == 1


def test_concurrent_run_cannot_bypass_operation_ownership_guard() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handler(_: ValidatedToolArguments) -> ToolResult:
        started.set()
        release.wait(2)
        finished.set()
        return ToolResult.success({"late": True})

    adapter = ScriptedAdapter(_tool(), _terminal())
    orchestrator = _orchestrator(adapter, _registry(handler, 0.02))

    async def scenario() -> tuple[AgentTerminalResult, AgentTerminalResult]:
        first_task = asyncio.create_task(orchestrator.run(_task()))
        assert await asyncio.to_thread(started.wait, 1) is True
        second_task = asyncio.create_task(orchestrator.run(_task()))
        first_result = await first_task
        second_result = await second_task
        release.set()
        assert await asyncio.to_thread(finished.wait, 1) is True
        return first_result, second_result

    first_result, second_result = asyncio.run(scenario())

    assert first_result.failure is not None
    assert first_result.failure.code is AgentErrorCode.TOOL_TIMEOUT
    assert second_result.failure is not None
    assert second_result.failure.code is AgentErrorCode.TOOL_EXECUTION_IN_PROGRESS
    assert len(adapter.requests) == 1


def test_caller_cancellation_returns_unknown_lifecycle_without_late_reentry() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    handler_calls = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal handler_calls
        handler_calls += 1
        started.set()
        release.wait(2)
        finished.set()
        return ToolResult.success({"late": True})

    adapter = ScriptedAdapter(_tool(), _terminal())
    orchestrator = _orchestrator(adapter, _registry(handler, 1))

    async def scenario() -> AgentTerminalResult:
        run_task = asyncio.create_task(orchestrator.run(_task()))
        assert await asyncio.to_thread(started.wait, 1) is True
        run_task.cancel()
        result = await run_task
        release.set()
        assert await asyncio.to_thread(finished.wait, 1) is True
        return result

    result = asyncio.run(scenario())

    assert result.state is AgentState.CANCELLED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.TASK_CANCELLED
    assert result.tool_execution_lifecycle is not None
    lifecycle = result.tool_execution_lifecycle
    assert lifecycle.state is ToolExecutionLifecycleState.CANCELLATION_REQUESTED
    assert lifecycle.execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert lifecycle.operation_terminated is not True
    assert handler_calls == 1
    assert len(adapter.requests) == 1


def test_model_timeout_remains_fail_closed_without_fallback() -> None:
    class SlowAdapter(ScriptedAdapter):
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            await asyncio.sleep(0.05)
            return await super().propose(request)

    adapter = SlowAdapter(_terminal())
    result = asyncio.run(
        AgentOrchestrator(
            adapter,
            _registry(lambda _: ToolResult.success({}), 1),
            PermissionPolicy.from_values({PermissionClass.READ_ONLY, PermissionClass.TERMINAL}),
            limits=AgentLimits(model_proposal_timeout_seconds=0.01),
        ).run(_task())
    )

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.MODEL_TIMEOUT
    assert adapter.requests == []


def test_unknown_outcome_cannot_claim_termination_and_serializes_safely() -> None:
    with pytest.raises(ValueError):
        ToolExecutionLifecycle(
            state=ToolExecutionLifecycleState.DEADLINE_EXCEEDED,
            request_deadline_exceeded=True,
            cancellation_requested=False,
            operation_terminated=True,
            execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
            execution_outcome_known=False,
        )

    lifecycle = ToolExecutionLifecycle(
        state=ToolExecutionLifecycleState.DEADLINE_EXCEEDED,
        request_deadline_exceeded=True,
        cancellation_requested=False,
        operation_terminated=None,
        execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
        execution_outcome_known=False,
    )
    json.dumps(lifecycle.to_dict(), allow_nan=False)
    assert isinstance(lifecycle.to_dict()["state"], str)


@pytest.mark.parametrize(
    "state,outcome,terminated",
    [
        (
            ToolExecutionLifecycleState.RUNNING,
            ExecutionOutcome.KNOWN_SUCCESS,
            True,
        ),
        (
            ToolExecutionLifecycleState.COMPLETED,
            ExecutionOutcome.UNKNOWN_OUTCOME,
            None,
        ),
        (
            ToolExecutionLifecycleState.FAILED,
            ExecutionOutcome.KNOWN_FAILURE,
            False,
        ),
        (
            ToolExecutionLifecycleState.TERMINATED,
            ExecutionOutcome.UNKNOWN_OUTCOME,
            None,
        ),
    ],
)
def test_lifecycle_rejects_contradictory_state_and_outcome(
    state: ToolExecutionLifecycleState,
    outcome: ExecutionOutcome,
    terminated: bool | None,
) -> None:
    with pytest.raises(ValueError):
        ToolExecutionLifecycle(
            state=state,
            request_deadline_exceeded=False,
            cancellation_requested=False,
            operation_terminated=terminated,
            execution_outcome=outcome,
            execution_outcome_known=outcome is not ExecutionOutcome.UNKNOWN_OUTCOME,
        )
