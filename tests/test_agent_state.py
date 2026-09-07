"""Slice 1 deterministic task-state machine tests."""

from __future__ import annotations

import pytest

from aaos.agent.contracts import AgentErrorCode, AgentLimits, AgentState, AgentTask, AgentTaskId
from aaos.agent.state import (
    LEGAL_STATE_TRANSITIONS,
    TERMINAL_STATES,
    AgentStateMachine,
    InvalidAgentTransition,
)


def test_initial_state_and_every_legal_transition_are_deterministic() -> None:
    machine = AgentStateMachine(AgentTask(AgentTaskId("task-001"), "request"))

    assert machine.state is AgentState.CREATED
    assert machine.step_count == 0
    machine.transition(AgentState.PLANNING)
    machine.transition(AgentState.TOOL_PENDING)
    machine.transition(AgentState.TOOL_RUNNING)
    machine.transition(AgentState.OBSERVING)
    machine.transition(AgentState.PLANNING)
    machine.transition(AgentState.COMPLETED)

    assert str(machine.state) == AgentState.COMPLETED.value
    assert machine.step_count == 6
    assert all(step.step_number == index for index, step in enumerate(machine.history, 1))


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in LEGAL_STATE_TRANSITIONS.items()
        for target in AgentState
        if source not in TERMINAL_STATES and target not in targets
    ],
)
def test_every_unlisted_transition_is_rejected(source: AgentState, target: AgentState) -> None:
    machine = AgentStateMachine(AgentTask(AgentTaskId("task-001"), "request"), initial_state=source)

    with pytest.raises(InvalidAgentTransition, match="INVALID_STATE_TRANSITION"):
        machine.transition(target)


@pytest.mark.parametrize(
    "terminal", [AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED]
)
def test_terminal_states_are_immutable(terminal: AgentState) -> None:
    machine = AgentStateMachine(
        AgentTask(AgentTaskId("task-001"), "request"), initial_state=terminal
    )

    with pytest.raises(InvalidAgentTransition, match="TASK_ALREADY_TERMINAL"):
        machine.transition(AgentState.PLANNING)
    with pytest.raises(InvalidAgentTransition, match="TASK_ALREADY_TERMINAL"):
        machine.transition(terminal)


def test_failure_and_cancellation_are_legal_terminal_paths() -> None:
    failed = AgentStateMachine(AgentTask(AgentTaskId("task-f"), "request"))
    failed.transition(AgentState.PLANNING)
    failed.transition(AgentState.FAILED)
    assert failed.state is AgentState.FAILED

    cancelled = AgentStateMachine(AgentTask(AgentTaskId("task-c"), "request"))
    cancelled.cancel()
    assert cancelled.state is AgentState.CANCELLED


def test_step_limit_is_enforced_before_state_mutation() -> None:
    machine = AgentStateMachine(
        AgentTask(AgentTaskId("task-001"), "request"),
        limits=AgentLimits(max_agent_steps=1),
    )
    machine.transition(AgentState.PLANNING)

    with pytest.raises(InvalidAgentTransition, match=AgentErrorCode.STEP_LIMIT_EXCEEDED.value):
        machine.transition(AgentState.TOOL_PENDING)
    assert machine.state is AgentState.PLANNING
    assert machine.step_count == 1


def test_unknown_state_input_and_model_state_injection_fail_closed() -> None:
    machine = AgentStateMachine(AgentTask(AgentTaskId("task-001"), "request"))

    with pytest.raises((TypeError, ValueError)):
        machine.transition("COMPLETED")  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        AgentTask(AgentTaskId("task-001"), "request", state="COMPLETED")  # type: ignore[arg-type]


def test_state_machine_history_is_immutable_and_authoritative() -> None:
    machine = AgentStateMachine(AgentTask(AgentTaskId("task-001"), "request"))
    machine.transition(AgentState.PLANNING, proposal_fingerprint="a" * 64)

    assert isinstance(machine.history, tuple)
    with pytest.raises(AttributeError):
        machine.history.append(None)  # type: ignore[attr-defined]
    assert machine.state is AgentState.PLANNING
