"""Trusted Phase 5 task state machine."""

from __future__ import annotations

from collections.abc import Mapping

from .contracts import AgentErrorCode, AgentLimits, AgentState, AgentStep, AgentTask

LEGAL_STATE_TRANSITIONS: Mapping[AgentState, frozenset[AgentState]] = {
    AgentState.CREATED: frozenset({AgentState.PLANNING, AgentState.CANCELLED}),
    AgentState.PLANNING: frozenset(
        {AgentState.TOOL_PENDING, AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}
    ),
    AgentState.TOOL_PENDING: frozenset(
        {AgentState.TOOL_RUNNING, AgentState.FAILED, AgentState.CANCELLED}
    ),
    AgentState.TOOL_RUNNING: frozenset(
        {AgentState.OBSERVING, AgentState.FAILED, AgentState.CANCELLED}
    ),
    AgentState.OBSERVING: frozenset(
        {AgentState.PLANNING, AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}
    ),
    AgentState.COMPLETED: frozenset(),
    AgentState.FAILED: frozenset(),
    AgentState.CANCELLED: frozenset(),
}

TERMINAL_STATES = frozenset({AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED})


class InvalidAgentTransition(ValueError):
    """A safe, deterministic state transition failure."""

    def __init__(self, code: AgentErrorCode, current: AgentState, target: AgentState) -> None:
        self.code = code
        self.current = current
        self.target = target
        super().__init__(f"{code.value}: {current.value} -> {target.value}")


class AgentStateMachine:
    """Application-owned state and monotonic transition history."""

    def __init__(
        self,
        task: AgentTask,
        *,
        limits: AgentLimits | None = None,
        initial_state: AgentState = AgentState.CREATED,
    ) -> None:
        if not isinstance(task, AgentTask):
            raise TypeError("task must be an AgentTask")
        if not isinstance(initial_state, AgentState):
            raise TypeError("initial_state must be an AgentState")
        self._task = task
        self._limits = limits if limits is not None else AgentLimits()
        self._state = initial_state
        self._history: list[AgentStep] = []

    @property
    def task(self) -> AgentTask:
        return self._task

    @property
    def limits(self) -> AgentLimits:
        return self._limits

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def current_state(self) -> AgentState:
        return self._state

    @property
    def step_count(self) -> int:
        return len(self._history)

    @property
    def history(self) -> tuple[AgentStep, ...]:
        return tuple(self._history)

    def transition(
        self,
        target: AgentState,
        *,
        proposal_fingerprint: str = "0" * 64,
        tool_call_fingerprint: str | None = None,
        outcome_code: AgentErrorCode | None = None,
    ) -> AgentStep:
        if not isinstance(target, AgentState):
            raise TypeError("target must be an AgentState")
        if self._state in TERMINAL_STATES:
            raise InvalidAgentTransition(AgentErrorCode.TASK_ALREADY_TERMINAL, self._state, target)
        if target not in LEGAL_STATE_TRANSITIONS[self._state]:
            raise InvalidAgentTransition(
                AgentErrorCode.INVALID_STATE_TRANSITION, self._state, target
            )
        if self.step_count >= self._limits.max_agent_steps:
            raise InvalidAgentTransition(AgentErrorCode.STEP_LIMIT_EXCEEDED, self._state, target)
        step = AgentStep(
            step_number=self.step_count + 1,
            from_state=self._state,
            to_state=target,
            proposal_fingerprint=proposal_fingerprint,
            tool_call_fingerprint=tool_call_fingerprint,
            outcome_code=outcome_code,
        )
        self._history.append(step)
        self._state = target
        return step

    def cancel(self) -> AgentStep:
        return self.transition(AgentState.CANCELLED, outcome_code=AgentErrorCode.TASK_CANCELLED)


__all__ = [
    "LEGAL_STATE_TRANSITIONS",
    "TERMINAL_STATES",
    "AgentStateMachine",
    "InvalidAgentTransition",
]
