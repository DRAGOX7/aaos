"""C7 safe backend event-envelope and sink tests."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentProposal,
    AgentState,
    AgentTask,
    AgentTaskId,
    ExecutionOutcome,
    PermissionClass,
    TerminalResultKind,
    ToolExecutionLifecycleState,
    ToolName,
    ToolResult,
)
from aaos.agent.events import (
    AgentEvent,
    AgentEventKind,
    AgentRunId,
    InMemoryAgentEventSink,
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


def _event(**overrides: object) -> AgentEvent:
    values: dict[str, object] = {
        "run_id": AgentRunId("run-1"),
        "sequence_number": 1,
        "event_kind": AgentEventKind.TASK_STARTED,
        "task_id": AgentTaskId("task-1"),
    }
    values.update(overrides)
    return AgentEvent(**values)  # type: ignore[arg-type]


def test_event_envelope_is_immutable_and_versioned() -> None:
    event = _event()

    assert event.schema_version == "phase5-agent-event-v1"
    with pytest.raises(FrozenInstanceError):
        event.sequence_number = 2  # type: ignore[misc]


def test_event_fields_are_explicit_without_a_payload_escape_hatch() -> None:
    event = _event()
    serialized = event.to_dict()

    assert "payload" not in serialized
    assert "metadata" not in serialized
    assert serialized["event_kind"] == "TASK_STARTED"
    assert serialized["run_id"] == "run-1"


def test_event_identifiers_are_trusted_and_content_independent() -> None:
    run_id = AgentRunId.new()
    assert run_id.value != "question text"
    assert "question" not in run_id.value
    with pytest.raises(ValueError):
        AgentRunId("../../secret")


def test_event_rejects_untrusted_enum_and_identifier_values() -> None:
    with pytest.raises(TypeError):
        _event(event_kind="TOOL_STARTED")
    with pytest.raises(ValueError):
        _event(sequence_number=0)
    with pytest.raises(TypeError):
        _event(tool_name="python")


def test_tool_lifecycle_event_requires_truthful_unknown_outcome() -> None:
    event = _event(
        event_kind=AgentEventKind.TOOL_DEADLINE_EXCEEDED,
        step_index=1,
        tool_name=ToolName.RAG_QUERY,
        tool_contract_version="phase5-tool-contract-v1",
        lifecycle_state=ToolExecutionLifecycleState.DEADLINE_EXCEEDED,
        execution_outcome=ExecutionOutcome.UNKNOWN_OUTCOME,
        error_code=AgentErrorCode.TOOL_TIMEOUT,
    )

    assert event.execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME
    assert event.lifecycle_state is ToolExecutionLifecycleState.DEADLINE_EXCEEDED


def test_event_sink_collects_in_trusted_sequence() -> None:
    sink = InMemoryAgentEventSink()
    sink.emit(_event(sequence_number=1))
    sink.emit(
        _event(
            sequence_number=2,
            event_kind=AgentEventKind.TASK_FINISHED,
            terminal_state=AgentState.COMPLETED,
            terminal_result_kind=TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL,
        )
    )

    assert [event.sequence_number for event in sink.events] == [1, 2]
    with pytest.raises(AttributeError):
        sink.events.append(_event())  # type: ignore[attr-defined]


class _ScriptedAdapter:
    def __init__(self, *proposals: ProposalResult) -> None:
        self._proposals = list(proposals)
        self.calls = 0

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        self.calls += 1
        return self._proposals.pop(0)


def _task() -> AgentTask:
    return AgentTask(AgentTaskId("task-1"), "Read task state")


def _registry(
    handler: Callable[[ValidatedToolArguments], ToolResult], timeout: float = 5
) -> ToolRegistry:
    definition = ToolDefinition(
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
    )
    return ToolRegistry((definition,))


def _tool_proposal() -> ProposalResult:
    return ProposalResult.success(
        AgentProposal.from_mapping(
            {
                "tool_call": {
                    "name": ToolName.READ_TASK_STATE.value,
                    "arguments": {"task_id": "task-1"},
                }
            }
        )
    )


def _terminal_proposal(text: str = "done") -> ProposalResult:
    return ProposalResult.success(AgentProposal(terminal_text=text))


def _policy() -> PermissionPolicy:
    return PermissionPolicy.from_values({PermissionClass.READ_ONLY, PermissionClass.TERMINAL})


def test_orchestrator_emits_trusted_run_policy_and_terminal_events() -> None:
    sink = InMemoryAgentEventSink()
    adapter = _ScriptedAdapter(_terminal_proposal())
    result = asyncio.run(
        AgentOrchestrator(
            adapter, _registry(lambda _: ToolResult.success({})), _policy(), event_sink=sink
        ).run(_task())
    )

    assert result.state is AgentState.COMPLETED
    assert [event.event_kind for event in sink.events] == [
        AgentEventKind.TASK_STARTED,
        AgentEventKind.RUNTIME_POLICY_VALIDATED,
        AgentEventKind.PROPOSAL_ACCEPTED,
        AgentEventKind.TERMINAL_RESULT_EMITTED,
        AgentEventKind.TASK_FINISHED,
    ]
    assert [event.sequence_number for event in sink.events] == [1, 2, 3, 4, 5]
    assert (
        sink.events[1].policy_fingerprint
        == AgentOrchestrator(
            _ScriptedAdapter(_terminal_proposal()),
            _registry(lambda _: ToolResult.success({})),
            _policy(),
        ).runtime_policy_fingerprint
    )


def test_tool_events_bind_contract_and_safe_outcome() -> None:
    sink = InMemoryAgentEventSink()
    adapter = _ScriptedAdapter(_tool_proposal())
    result = asyncio.run(
        AgentOrchestrator(
            adapter, _registry(lambda _: ToolResult.success({})), _policy(), event_sink=sink
        ).run(_task())
    )

    assert result.state is AgentState.FAILED
    kinds = [event.event_kind for event in sink.events]
    assert AgentEventKind.TOOL_STARTED in kinds
    assert AgentEventKind.TOOL_COMPLETED in kinds
    completed = next(
        event for event in sink.events if event.event_kind is AgentEventKind.TOOL_COMPLETED
    )
    assert completed.tool_name is ToolName.READ_TASK_STATE
    assert completed.tool_contract_version == "phase5-tool-contract-v1"
    assert completed.execution_outcome is ExecutionOutcome.KNOWN_SUCCESS


def test_deadline_event_does_not_emit_late_success() -> None:
    started = threading.Event()
    release = threading.Event()

    def handler(_: ValidatedToolArguments) -> ToolResult:
        started.set()
        release.wait(timeout=2)
        return ToolResult.success({"late": True})

    sink = InMemoryAgentEventSink()
    result = asyncio.run(
        AgentOrchestrator(
            _ScriptedAdapter(_tool_proposal()),
            _registry(handler, timeout=0.01),
            _policy(),
            event_sink=sink,
        ).run(_task())
    )
    assert started.is_set()
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.TOOL_TIMEOUT
    deadline = [
        event for event in sink.events if event.event_kind is AgentEventKind.TOOL_DEADLINE_EXCEEDED
    ]
    assert len(deadline) == 1
    assert deadline[0].execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME

    before = tuple(sink.events)
    release.set()
    time.sleep(0.05)
    assert tuple(sink.events) == before


def test_invalid_event_sink_fails_before_model_or_tool_call() -> None:
    adapter = _ScriptedAdapter(_terminal_proposal())
    calls = 0

    def handler(_: ValidatedToolArguments) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success({})

    with pytest.raises(TypeError, match="event_sink"):
        AgentOrchestrator(
            adapter,
            _registry(handler),
            _policy(),
            event_sink=object(),  # type: ignore[arg-type]
        )
    assert adapter.calls == 0
    assert calls == 0


def test_event_sink_failure_is_not_exposed_or_authoritative() -> None:
    class FailingSink:
        def emit(self, event: AgentEvent) -> None:
            raise RuntimeError("secret sink failure")

    adapter = _ScriptedAdapter(_terminal_proposal("safe"))
    result = asyncio.run(
        AgentOrchestrator(
            adapter,
            _registry(lambda _: ToolResult.success({})),
            _policy(),
            event_sink=FailingSink(),
        ).run(_task())
    )

    assert result.state is AgentState.COMPLETED
    assert result.result_text == "safe"
    assert "secret" not in str(result.to_dict())


class _GroundedHandler:
    trusted_phase4_output = True

    def __call__(self, _: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(
            {
                "answer_text": "trusted",
                "citations": [
                    {
                        "claim_id": "c1",
                        "document_version_id": 1,
                        "document_title": "source",
                        "page_start": 1,
                        "page_end": 1,
                    }
                ],
                "content_revision": 1,
                "generation_id": 1,
                "language": "en",
                "rendered_text": "trusted",
                "status": "ANSWER",
            }
        )


def test_grounded_terminal_event_uses_trusted_lineage_category() -> None:
    class Adapter(_ScriptedAdapter):
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            self.calls += 1
            if self.calls == 1:
                return _tool_proposal()
            return ProposalResult.success(AgentProposal(grounded_result_ref="rag-result-1"))

    sink = InMemoryAgentEventSink()
    result = asyncio.run(
        AgentOrchestrator(Adapter(), _registry(_GroundedHandler()), _policy(), event_sink=sink).run(
            _task()
        )
    )

    assert result.result_kind is TerminalResultKind.GROUNDED_RAG_RESPONSE
    terminal = next(
        event for event in sink.events if event.event_kind is AgentEventKind.TERMINAL_RESULT_EMITTED
    )
    assert terminal.terminal_result_kind is TerminalResultKind.GROUNDED_RAG_RESPONSE
