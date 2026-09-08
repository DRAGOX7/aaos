"""Bounded trusted orchestration for the Phase 5 local agent loop.

The proposal adapter is an untrusted model boundary.  This module owns the
task state machine, invokes the static tool registry, and exposes only
sanitized observations back to that adapter.  It deliberately contains no
model, network, persistence, or dynamic-tool behavior.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import replace
from typing import Protocol

from .contracts import (
    AgentErrorCode,
    AgentFailure,
    AgentLimits,
    AgentState,
    AgentTask,
    AgentTerminalResult,
    ExecutionOutcome,
    GroundedRagResponse,
    PermissionClass,
    TerminalResultKind,
    ToolExecutionLifecycle,
    ToolExecutionLifecycleState,
    ToolObservation,
    ToolResult,
)
from .observations import ObservationSanitizationError
from .policy import PermissionPolicy
from .proposal import ProposalRequest, ProposalResult
from .registry import ToolInvocationError, ToolRegistry
from .runtime_policy import (
    MAX_AGENT_STEPS,
    MAX_OBSERVATION_HISTORY_SIZE,
    MAX_OBSERVATION_SIZE,
    MAX_OBSERVATIONS,
    MAX_TOOL_CALLS,
    EffectiveRuntimePolicy,
    compose_effective_runtime_policy,
)
from .state import TERMINAL_STATES, AgentStateMachine, InvalidAgentTransition


class ProposalSource(Protocol):
    """Structural interface implemented by the Slice 4 proposal adapter."""

    async def propose(self, request: ProposalRequest) -> ProposalResult: ...


async def _wait_for_event(event: asyncio.Event) -> object:
    """Adapt an event wait to the common task type used by lifecycle races."""

    await event.wait()
    return True


_SAFE_FAILURE_MESSAGES: dict[AgentErrorCode, str] = {
    AgentErrorCode.INVALID_PROPOSAL: "agent proposal was invalid",
    AgentErrorCode.UNKNOWN_TOOL: "requested tool is not available",
    AgentErrorCode.INVALID_TOOL_ARGUMENTS: "tool arguments were invalid",
    AgentErrorCode.SCOPE_DENIED: "requested scope was not authorized",
    AgentErrorCode.PERMISSION_DENIED: "tool permission was denied",
    AgentErrorCode.TOOL_NOT_EXECUTABLE: "requested tool is not executable",
    AgentErrorCode.TOOL_FAILED: "tool execution failed",
    AgentErrorCode.TOOL_TIMEOUT: "tool execution timed out",
    AgentErrorCode.TOOL_OUTCOME_UNKNOWN: "tool execution outcome is unknown",
    AgentErrorCode.TOOL_EXECUTION_IN_PROGRESS: "another tool execution is still in progress",
    AgentErrorCode.HANDLER_EXECUTION_FAILED: "tool execution failed",
    AgentErrorCode.STEP_LIMIT_EXCEEDED: "agent step limit was reached",
    AgentErrorCode.LOOP_DETECTED: "repeated tool call limit was reached",
    AgentErrorCode.OBSERVATION_SANITIZATION_FAILED: "tool observation could not be sanitized",
    AgentErrorCode.OBSERVATION_HISTORY_LIMIT_EXCEEDED: "tool observation history limit was reached",
    AgentErrorCode.INCONSISTENT_RUNTIME_POLICY: "runtime policy is inconsistent",
    AgentErrorCode.TASK_CANCELLED: "agent task was cancelled",
    AgentErrorCode.MODEL_TIMEOUT: "agent proposal model timed out",
    AgentErrorCode.MODEL_UNAVAILABLE: "agent proposal model is unavailable",
    AgentErrorCode.INTERNAL_AGENT_FAILURE: "agent execution failed safely",
    AgentErrorCode.INVALID_GROUNDED_REFERENCE: "grounded result reference was invalid",
}


class AgentOrchestrator:
    """Run one bounded primary agent task under trusted application control.

    The orchestrator never lets model data mutate authoritative state.  Each
    proposal is validated by ``ToolRegistry.execute`` and only a bounded,
    sanitized ``ToolObservation`` is sent to the next proposal step.
    """

    def __init__(
        self,
        proposal_adapter: ProposalSource,
        registry: ToolRegistry | None = None,
        permission_policy: PermissionPolicy | None = None,
        *,
        limits: AgentLimits | None = None,
        effective_policy: EffectiveRuntimePolicy | None = None,
        max_tool_calls: int | None = None,
        max_observations: int | None = None,
        max_observation_size: int | None = None,
        total_observation_history_size: int | None = None,
    ) -> None:
        if not callable(getattr(proposal_adapter, "propose", None)):
            raise TypeError("proposal_adapter must expose an async propose method")
        if registry is not None and not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if permission_policy is not None and not isinstance(permission_policy, PermissionPolicy):
            raise TypeError("permission_policy must be a PermissionPolicy")
        if limits is not None and not isinstance(limits, AgentLimits):
            raise TypeError("limits must be AgentLimits")
        self._proposal_adapter = proposal_adapter
        self._registry = registry if registry is not None else ToolRegistry.default()
        self._permission_policy = (
            permission_policy if permission_policy is not None else PermissionPolicy()
        )
        self._runtime_policy = compose_effective_runtime_policy(
            effective_policy=effective_policy,
            proposal_adapter=proposal_adapter,
            registry=self._registry,
            limits=limits,
            max_tool_calls=max_tool_calls,
            max_observations=max_observations,
            max_observation_size=max_observation_size,
            total_observation_history_size=total_observation_history_size,
        )
        self._limits = self._runtime_policy.to_agent_limits()
        self._max_tool_calls = self._runtime_policy.max_tool_calls
        self._max_observations = self._runtime_policy.max_observations
        self._max_observation_size = self._runtime_policy.max_observation_size
        self._total_observation_history_size = self._runtime_policy.total_observation_history_size
        self._cancel_requested = False
        self._cancel_signal: asyncio.Event | None = None
        self._active_operations: dict[int, asyncio.Task[object]] = {}
        self._run_lock = asyncio.Lock()

    @property
    def limits(self) -> AgentLimits:
        return self._limits

    @property
    def runtime_policy(self) -> EffectiveRuntimePolicy:
        return self._runtime_policy

    @property
    def runtime_policy_version(self) -> str:
        return self._runtime_policy.policy_version

    @property
    def runtime_policy_fingerprint(self) -> str:
        return self._runtime_policy.fingerprint

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def permission_policy(self) -> PermissionPolicy:
        return self._permission_policy

    @property
    def max_tool_calls(self) -> int:
        return self._max_tool_calls

    @property
    def max_observations(self) -> int:
        return self._max_observations

    @property
    def max_observation_size(self) -> int:
        return self._max_observation_size

    @property
    def total_observation_history_size(self) -> int:
        return self._total_observation_history_size

    def cancel(self) -> None:
        """Request cancellation before the next trusted orchestration boundary."""

        self._cancel_requested = True
        if self._cancel_signal is not None:
            self._cancel_signal.set()

    async def run(
        self, task: AgentTask, cancel_event: asyncio.Event | None = None
    ) -> AgentTerminalResult:
        """Serialize task execution to preserve operation ownership."""

        async with self._run_lock:
            return await self._run_impl(task, cancel_event)

    async def _run_impl(
        self, task: AgentTask, cancel_event: asyncio.Event | None = None
    ) -> AgentTerminalResult:
        """Execute one task until a trusted terminal result is reached."""

        if not isinstance(task, AgentTask):
            raise TypeError("task must be an AgentTask")
        if cancel_event is not None and not isinstance(cancel_event, asyncio.Event):
            raise TypeError("cancel_event must be an asyncio.Event")

        cancellation_requested_before_run = self._cancel_requested
        self._cancel_requested = False
        self._cancel_signal = asyncio.Event()
        machine = self._new_state_machine(task)
        proposal_steps = 0
        tool_calls = 0
        observations: list[ToolObservation] = []
        grounded_results: dict[str, GroundedRagResponse] = {}
        grounded_result_counter = 0
        last_tool_execution_lifecycle: ToolExecutionLifecycle | None = None
        repeated_calls: dict[str, int] = {}
        last_proposal_fingerprint = _stable_fingerprint({"task_id": task.task_id.value})

        if cancellation_requested_before_run or self._is_cancelled(cancel_event):
            machine.cancel()
            return self._terminal_failure(
                task,
                machine,
                AgentErrorCode.TASK_CANCELLED,
                step_count=proposal_steps,
            )
        try:
            machine.transition(AgentState.PLANNING)
        except InvalidAgentTransition:
            return self._internal_failure(task, machine, proposal_steps)
        if self._active_operations:
            return self._failure(
                task,
                machine,
                AgentErrorCode.TOOL_EXECUTION_IN_PROGRESS,
                proposal_steps,
                last_proposal_fingerprint,
            )

        while True:
            if self._is_cancelled(cancel_event):
                return self._cancelled(
                    task,
                    machine,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if proposal_steps >= self._limits.max_agent_steps:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.STEP_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            if _observation_history_size(observations) > self._total_observation_history_size:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.OBSERVATION_HISTORY_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                )

            try:
                request = ProposalRequest(task.request_text, tuple(observations))
            except (TypeError, ValueError):
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.OBSERVATION_SANITIZATION_FAILED,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            try:
                result = await asyncio.wait_for(
                    self._proposal_adapter.propose(request),
                    timeout=self._limits.model_proposal_timeout_seconds,
                )
            except asyncio.CancelledError:
                return self._cancelled(task, machine, proposal_steps, last_proposal_fingerprint)
            except asyncio.TimeoutError:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.MODEL_TIMEOUT,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            except Exception:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.MODEL_UNAVAILABLE,
                    proposal_steps,
                    last_proposal_fingerprint,
                )

            proposal_steps += 1
            if self._is_cancelled(cancel_event):
                return self._cancelled(
                    task,
                    machine,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if not isinstance(result, ProposalResult):
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INVALID_PROPOSAL,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            if not result.ok or result.proposal is None:
                failure = result.failure
                if failure is None:
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.INVALID_PROPOSAL,
                        proposal_steps,
                        last_proposal_fingerprint,
                    )
                last_proposal_fingerprint = _stable_fingerprint(failure.to_dict())
                return self._failure(
                    task,
                    machine,
                    failure.code,
                    proposal_steps,
                    last_proposal_fingerprint,
                    failure=failure,
                )

            proposal = result.proposal
            last_proposal_fingerprint = _stable_fingerprint(proposal.to_dict())
            if proposal.grounded_result_ref is not None:
                if not self._terminal_permission_granted():
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.PERMISSION_DENIED,
                        proposal_steps,
                        last_proposal_fingerprint,
                    )
                grounded_result = grounded_results.get(proposal.grounded_result_ref)
                if grounded_result is None or grounded_result.task_id != task.task_id:
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.INVALID_GROUNDED_REFERENCE,
                        proposal_steps,
                        last_proposal_fingerprint,
                    )
                return self._complete_grounded(
                    task,
                    machine,
                    grounded_result,
                    proposal_steps,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if proposal.terminal_text is not None:
                if not self._terminal_permission_granted():
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.PERMISSION_DENIED,
                        proposal_steps,
                        last_proposal_fingerprint,
                    )
                # A control/conversational proposal is valid only when no
                # trusted Phase 4 result has been retained. It must never
                # replace a grounded answer or refusal with model-authored
                # text.
                if grounded_results:
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.INVALID_PROPOSAL,
                        proposal_steps,
                        last_proposal_fingerprint,
                    )
                return self._complete_control(
                    task,
                    machine,
                    proposal.terminal_text,
                    proposal_steps,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if proposal.tool_call is None:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INVALID_PROPOSAL,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            if tool_calls >= self._max_tool_calls:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.STEP_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                )

            try:
                validated_call = self._registry.prepare(
                    proposal,
                    self._permission_policy,
                    trusted_task_scope=task.initial_scope,
                )
            except ToolInvocationError as error:
                return self._failure(
                    task,
                    machine,
                    error.code,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            except Exception:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INTERNAL_AGENT_FAILURE,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            tool_key = _stable_fingerprint(
                {
                    "arguments": validated_call.arguments.to_mapping(),
                    "name": validated_call.name.value,
                }
            )
            repeated_calls[tool_key] = repeated_calls.get(tool_key, 0) + 1
            if repeated_calls[tool_key] > self._limits.max_identical_tool_call_repetitions:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.LOOP_DETECTED,
                    proposal_steps,
                    last_proposal_fingerprint,
                )

            try:
                machine.transition(
                    AgentState.TOOL_PENDING,
                    proposal_fingerprint=last_proposal_fingerprint,
                    tool_call_fingerprint=tool_key,
                )
                machine.transition(
                    AgentState.TOOL_RUNNING,
                    proposal_fingerprint=last_proposal_fingerprint,
                    tool_call_fingerprint=tool_key,
                )
            except InvalidAgentTransition:
                return self._internal_failure(task, machine, proposal_steps)

            tool_calls += 1
            tool_result: ToolResult | None = None
            try:
                definition = self._registry.get(validated_call.name)
            except ToolInvocationError:
                definition = None
            except Exception:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INTERNAL_AGENT_FAILURE,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            if definition is None:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INCONSISTENT_RUNTIME_POLICY,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            timeout_seconds = self._runtime_policy.timeout_for(validated_call.name)
            if timeout_seconds is None:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.INCONSISTENT_RUNTIME_POLICY,
                    proposal_steps,
                    last_proposal_fingerprint,
                )
            try:
                operation = asyncio.create_task(
                    asyncio.to_thread(
                        self._registry.execute,
                        proposal,
                        self._permission_policy,
                        trusted_task_scope=task.initial_scope,
                    )
                )
                operation_key = id(operation)
                self._active_operations[operation_key] = operation

                def consume_operation(
                    completed: asyncio.Task[object], *, key: int = operation_key
                ) -> None:
                    try:
                        completed.result()
                    except BaseException:
                        # The result is intentionally discarded after a
                        # terminal timeout/cancellation.  Consuming it
                        # prevents an unhandled late exception while the
                        # operation remains owned by this orchestrator.
                        pass
                    self._active_operations.pop(key, None)

                operation.add_done_callback(consume_operation)
                tool_result, last_tool_execution_lifecycle = await self._await_tool_operation(
                    operation, timeout_seconds, cancel_event
                )
                if last_tool_execution_lifecycle.state is (
                    ToolExecutionLifecycleState.DEADLINE_EXCEEDED
                ):
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.TOOL_TIMEOUT,
                        proposal_steps,
                        last_proposal_fingerprint,
                        tool_execution_lifecycle=last_tool_execution_lifecycle,
                    )
                if last_tool_execution_lifecycle.state is (
                    ToolExecutionLifecycleState.CANCELLATION_REQUESTED
                ):
                    return self._cancelled(
                        task,
                        machine,
                        proposal_steps,
                        last_proposal_fingerprint,
                        tool_execution_lifecycle=last_tool_execution_lifecycle,
                    )
                if last_tool_execution_lifecycle.state is ToolExecutionLifecycleState.UNKNOWN:
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.TOOL_OUTCOME_UNKNOWN,
                        proposal_steps,
                        last_proposal_fingerprint,
                        tool_execution_lifecycle=last_tool_execution_lifecycle,
                    )
            except asyncio.CancelledError:
                return self._cancelled(
                    task,
                    machine,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=_cancellation_requested_lifecycle(),
                )
            except Exception:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.HANDLER_EXECUTION_FAILED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if self._is_cancelled(cancel_event):
                return self._cancelled(
                    task,
                    machine,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if not isinstance(tool_result, ToolResult):
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.HANDLER_EXECUTION_FAILED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if not tool_result.ok:
                error_code = (
                    tool_result.error.code
                    if tool_result.error is not None
                    else AgentErrorCode.TOOL_FAILED
                )
                return self._failure(
                    task,
                    machine,
                    error_code,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )

            captured_grounded_result: GroundedRagResponse | None = None
            trusted_phase4_output = (
                definition is not None
                and getattr(definition.handler, "trusted_phase4_output", False) is True
            )
            if trusted_phase4_output:
                try:
                    grounded_result_counter += 1
                    # This is a task-local opaque handle, intentionally not a
                    # hash of answer/citation/provenance content. The trusted
                    # map below is the only authority that resolves it.
                    result_ref = f"rag-result-{grounded_result_counter}"
                    captured_grounded_result = GroundedRagResponse.from_tool_result(
                        task.task_id, result_ref, tool_result
                    )
                except (TypeError, ValueError, KeyError):
                    return self._failure(
                        task,
                        machine,
                        AgentErrorCode.HANDLER_EXECUTION_FAILED,
                        proposal_steps,
                        last_proposal_fingerprint,
                        tool_execution_lifecycle=last_tool_execution_lifecycle,
                    )

            try:
                machine.transition(
                    AgentState.OBSERVING,
                    proposal_fingerprint=last_proposal_fingerprint,
                    tool_call_fingerprint=tool_key,
                )
                observation = definition.project_observation(
                    tool_result,
                    max_size=self._max_observation_size,
                )
                if captured_grounded_result is not None:
                    observation = ToolObservation(
                        kind=observation.kind,
                        status=observation.status,
                        text=observation.text,
                        fields=(
                            *observation.fields,
                            ("grounded_result_ref", captured_grounded_result.result_ref),
                        ),
                    )
                    if len(_stable_json(observation.to_dict())) > self._max_observation_size:
                        raise ObservationSanitizationError()
            except (TypeError, ValueError, ObservationSanitizationError):
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.OBSERVATION_SANITIZATION_FAILED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )

            observations.append(observation)
            if captured_grounded_result is not None:
                grounded_results[captured_grounded_result.result_ref] = captured_grounded_result
            if len(observations) > self._max_observations:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.OBSERVATION_SANITIZATION_FAILED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if _observation_history_size(observations) > self._total_observation_history_size:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.OBSERVATION_HISTORY_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if proposal_steps >= self._limits.max_agent_steps:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.STEP_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            if tool_calls >= self._max_tool_calls:
                return self._failure(
                    task,
                    machine,
                    AgentErrorCode.STEP_LIMIT_EXCEEDED,
                    proposal_steps,
                    last_proposal_fingerprint,
                    tool_execution_lifecycle=last_tool_execution_lifecycle,
                )
            try:
                machine.transition(
                    AgentState.PLANNING,
                    proposal_fingerprint=last_proposal_fingerprint,
                    tool_call_fingerprint=tool_key,
                )
            except InvalidAgentTransition:
                return self._internal_failure(task, machine, proposal_steps)

    async def _await_tool_operation(
        self,
        operation: asyncio.Task[object],
        timeout_seconds: float,
        cancel_event: asyncio.Event | None,
    ) -> tuple[ToolResult | None, ToolExecutionLifecycle]:
        """Wait for a tool without mistaking waiter cancellation for termination.

        ``asyncio.to_thread`` cannot safely stop a synchronous worker.  The
        operation task therefore remains owned by the orchestrator after a
        deadline or cancellation request, while this method returns a
        truthful unknown outcome to the agent state machine.
        """

        cancellation_watchers: list[asyncio.Task[object]] = []
        if cancel_event is not None:
            cancellation_watchers.append(asyncio.create_task(_wait_for_event(cancel_event)))
        if self._cancel_signal is not None:
            cancellation_watchers.append(asyncio.create_task(_wait_for_event(self._cancel_signal)))
        waitables: set[asyncio.Future[object]] = {operation, *cancellation_watchers}
        try:
            done, _ = await asyncio.wait(
                waitables,
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for watcher in cancellation_watchers:
                if not watcher.done():
                    watcher.cancel()
            if cancellation_watchers:
                await asyncio.gather(*cancellation_watchers, return_exceptions=True)

        if operation in done:
            try:
                result = operation.result()
            except BaseException:
                return None, _unknown_tool_lifecycle()
            if not isinstance(result, ToolResult):
                return None, _unknown_tool_lifecycle()
            return result, _known_tool_lifecycle(result)

        if any(watcher in done for watcher in cancellation_watchers):
            return None, ToolExecutionLifecycle(
                state=ToolExecutionLifecycleState.CANCELLATION_REQUESTED,
                request_deadline_exceeded=False,
                cancellation_requested=True,
                operation_terminated=None,
                execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
                execution_outcome_known=False,
                resource_cleanup_requested=False,
                resource_cleanup_complete=None,
            )
        return None, ToolExecutionLifecycle(
            state=ToolExecutionLifecycleState.DEADLINE_EXCEEDED,
            request_deadline_exceeded=True,
            cancellation_requested=False,
            operation_terminated=None,
            execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
            execution_outcome_known=False,
            resource_cleanup_requested=False,
            resource_cleanup_complete=None,
        )

    def run_task(
        self, task: AgentTask, cancel_event: asyncio.Event | None = None
    ) -> Awaitable[AgentTerminalResult]:
        """Compatibility alias for callers that use ``run_task`` terminology."""

        return self.run(task, cancel_event)

    def orchestrate(
        self, task: AgentTask, cancel_event: asyncio.Event | None = None
    ) -> Awaitable[AgentTerminalResult]:
        """Compatibility alias for the primary ``run`` boundary."""

        return self.run(task, cancel_event)

    def _new_state_machine(self, task: AgentTask) -> AgentStateMachine:
        # AgentStateMachine counts state transitions, while this orchestrator
        # counts proposal cycles.  Keep the frozen state-machine API intact and
        # reserve enough trusted transition envelope for the configured cycles.
        transition_budget = max(self._limits.max_agent_steps, self._limits.max_agent_steps * 4 + 2)
        return AgentStateMachine(
            task,
            limits=replace(self._limits, max_agent_steps=transition_budget),
        )

    def _is_cancelled(self, cancel_event: asyncio.Event | None) -> bool:
        return self._cancel_requested or (cancel_event is not None and cancel_event.is_set())

    def _terminal_permission_granted(self) -> bool:
        return PermissionClass.TERMINAL in self._permission_policy.granted_permissions

    def _complete_control(
        self,
        task: AgentTask,
        machine: AgentStateMachine,
        text: str,
        step_count: int,
        *,
        tool_execution_lifecycle: ToolExecutionLifecycle | None = None,
    ) -> AgentTerminalResult:
        try:
            machine.transition(AgentState.COMPLETED)
        except InvalidAgentTransition:
            return self._internal_failure(task, machine, step_count)
        return AgentTerminalResult(
            task.task_id,
            AgentState.COMPLETED,
            step_count,
            result_text=text,
            result_kind=TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL,
            tool_execution_lifecycle=tool_execution_lifecycle,
        )

    def _complete_grounded(
        self,
        task: AgentTask,
        machine: AgentStateMachine,
        grounded_result: GroundedRagResponse,
        step_count: int,
        *,
        tool_execution_lifecycle: ToolExecutionLifecycle | None = None,
    ) -> AgentTerminalResult:
        try:
            machine.transition(AgentState.COMPLETED)
        except InvalidAgentTransition:
            return self._internal_failure(task, machine, step_count)
        return AgentTerminalResult(
            task.task_id,
            AgentState.COMPLETED,
            step_count,
            result_text=str(grounded_result.payload["rendered_text"]),
            result_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
            grounded_rag_response=grounded_result,
            tool_execution_lifecycle=tool_execution_lifecycle,
        )

    def _cancelled(
        self,
        task: AgentTask,
        machine: AgentStateMachine,
        step_count: int,
        proposal_fingerprint: str,
        *,
        tool_execution_lifecycle: ToolExecutionLifecycle | None = None,
    ) -> AgentTerminalResult:
        try:
            if machine.state not in TERMINAL_STATES:
                machine.transition(
                    AgentState.CANCELLED,
                    proposal_fingerprint=proposal_fingerprint,
                    outcome_code=AgentErrorCode.TASK_CANCELLED,
                )
        except InvalidAgentTransition:
            return self._internal_failure(task, machine, step_count)
        return self._terminal_failure(
            task,
            machine,
            AgentErrorCode.TASK_CANCELLED,
            step_count=step_count,
            tool_execution_lifecycle=tool_execution_lifecycle,
        )

    def _failure(
        self,
        task: AgentTask,
        machine: AgentStateMachine,
        code: AgentErrorCode,
        step_count: int,
        proposal_fingerprint: str,
        *,
        failure: AgentFailure | None = None,
        tool_execution_lifecycle: ToolExecutionLifecycle | None = None,
    ) -> AgentTerminalResult:
        safe_failure = failure or _safe_failure(code)
        try:
            if machine.state not in TERMINAL_STATES:
                machine.transition(
                    AgentState.FAILED,
                    proposal_fingerprint=proposal_fingerprint,
                    outcome_code=code,
                )
        except InvalidAgentTransition:
            safe_failure = _safe_failure(AgentErrorCode.INTERNAL_AGENT_FAILURE)
        return AgentTerminalResult(
            task.task_id,
            AgentState.FAILED,
            step_count,
            failure=safe_failure,
            tool_execution_lifecycle=tool_execution_lifecycle,
        )

    def _terminal_failure(
        self,
        task: AgentTask,
        machine: AgentStateMachine,
        code: AgentErrorCode,
        *,
        step_count: int,
        tool_execution_lifecycle: ToolExecutionLifecycle | None = None,
    ) -> AgentTerminalResult:
        return AgentTerminalResult(
            task.task_id,
            machine.state,
            step_count,
            failure=_safe_failure(code),
            tool_execution_lifecycle=tool_execution_lifecycle,
        )

    def _internal_failure(
        self, task: AgentTask, machine: AgentStateMachine, step_count: int
    ) -> AgentTerminalResult:
        return self._failure(
            task,
            machine,
            AgentErrorCode.INTERNAL_AGENT_FAILURE,
            step_count,
            _stable_fingerprint({"task_id": task.task_id.value}),
        )


def _observation_history_size(observations: list[ToolObservation]) -> int:
    return sum(len(_stable_json(observation.to_dict())) for observation in observations)


def _known_tool_lifecycle(result: ToolResult) -> ToolExecutionLifecycle:
    """Build a terminal lifecycle snapshot for an observed handler result."""

    succeeded = result.ok
    return ToolExecutionLifecycle(
        state=(
            ToolExecutionLifecycleState.COMPLETED
            if succeeded
            else ToolExecutionLifecycleState.FAILED
        ),
        request_deadline_exceeded=False,
        cancellation_requested=False,
        operation_terminated=True,
        execution_outcome=(
            ExecutionOutcome.KNOWN_SUCCESS if succeeded else ExecutionOutcome.KNOWN_FAILURE
        ),
        execution_outcome_known=True,
        resource_cleanup_requested=False,
        resource_cleanup_complete=None,
    )


def _unknown_tool_lifecycle() -> ToolExecutionLifecycle:
    """Build a snapshot when task completion cannot prove operation outcome."""

    return ToolExecutionLifecycle(
        state=ToolExecutionLifecycleState.UNKNOWN,
        request_deadline_exceeded=False,
        cancellation_requested=False,
        operation_terminated=None,
        execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
        execution_outcome_known=False,
        resource_cleanup_requested=False,
        resource_cleanup_complete=None,
    )


def _cancellation_requested_lifecycle() -> ToolExecutionLifecycle:
    """Build a lifecycle snapshot when the caller cancels an in-flight wait."""

    return ToolExecutionLifecycle(
        state=ToolExecutionLifecycleState.CANCELLATION_REQUESTED,
        request_deadline_exceeded=False,
        cancellation_requested=True,
        operation_terminated=None,
        execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
        execution_outcome_known=False,
        resource_cleanup_requested=False,
        resource_cleanup_complete=None,
    )


def _safe_failure(
    code: AgentErrorCode, *, retryable: bool = False, stage: str = "orchestrator"
) -> AgentFailure:
    message = _SAFE_FAILURE_MESSAGES.get(code, "agent execution failed safely")
    return AgentFailure(code, message, stage=stage, retryable=retryable)


def _stable_json(value: object) -> str:
    """Serialize already validated contract data, including JSON arrays."""

    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _stable_fingerprint(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "MAX_AGENT_STEPS",
    "MAX_OBSERVATION_SIZE",
    "MAX_OBSERVATION_HISTORY_SIZE",
    "MAX_OBSERVATIONS",
    "MAX_TOOL_CALLS",
    "AgentOrchestrator",
    "ProposalSource",
]
