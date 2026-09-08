"""Safe, immutable backend events for the Phase 5 agent lifecycle.

Events are trusted observations of application-owned execution.  They contain
only bounded identifiers and enums; model text, tool payloads, and arbitrary
metadata never cross this boundary.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite
from typing import Protocol

from .contracts import (
    AgentErrorCode,
    AgentState,
    AgentTaskId,
    ExecutionOutcome,
    TerminalResultKind,
    ToolExecutionLifecycleState,
    ToolName,
)

EVENT_SCHEMA_VERSION = "phase5-agent-event-v1"
MAX_EVENT_IDENTIFIER_CHARACTERS = 128
MAX_EVENT_SEQUENCE = 1_000_000_000
MAX_EVENT_STEP_INDEX = 1_000_000
MAX_EVENT_DURATION_MS = 86_400_000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class AgentEventKind(StrEnum):
    TASK_STARTED = "TASK_STARTED"
    RUNTIME_POLICY_VALIDATED = "RUNTIME_POLICY_VALIDATED"
    PROPOSAL_ACCEPTED = "PROPOSAL_ACCEPTED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_DEADLINE_EXCEEDED = "TOOL_DEADLINE_EXCEEDED"
    TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
    TERMINAL_RESULT_EMITTED = "TERMINAL_RESULT_EMITTED"
    TASK_FINISHED = "TASK_FINISHED"


@dataclass(frozen=True, slots=True)
class AgentRunId:
    """Trusted opaque identifier for one orchestrator run."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("run_id must be a non-blank string")
        normalized = self.value.strip()
        if len(normalized) > MAX_EVENT_IDENTIFIER_CHARACTERS or not _SAFE_IDENTIFIER.fullmatch(
            normalized
        ):
            raise ValueError("run_id must be a safe opaque identifier")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def new(cls) -> "AgentRunId":
        """Generate an application-owned, content-independent run identifier."""

        return cls(uuid.uuid4().hex)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        return self.value


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    normalized = value.strip()
    if len(normalized) > MAX_EVENT_IDENTIFIER_CHARACTERS or not _SAFE_IDENTIFIER.fullmatch(
        normalized
    ):
        raise ValueError(f"{name} must be a safe identifier")
    return normalized


def _sha256(value: object, name: str) -> str:
    normalized = _safe_identifier(value, name)
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Versioned content-free envelope emitted by trusted application code."""

    run_id: AgentRunId
    sequence_number: int
    event_kind: AgentEventKind
    task_id: AgentTaskId
    schema_version: str = EVENT_SCHEMA_VERSION
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    step_index: int | None = None
    tool_name: ToolName | None = None
    tool_contract_version: str | None = None
    lifecycle_state: ToolExecutionLifecycleState | None = None
    execution_outcome: ExecutionOutcome | None = None
    error_code: AgentErrorCode | None = None
    duration_ms: float | None = None
    policy_version: str | None = None
    policy_fingerprint: str | None = None
    terminal_state: AgentState | None = None
    terminal_result_kind: TerminalResultKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, AgentRunId):
            raise TypeError("run_id must be an AgentRunId")
        if isinstance(self.sequence_number, bool) or not isinstance(self.sequence_number, int):
            raise TypeError("sequence_number must be an integer")
        if not 0 < self.sequence_number <= MAX_EVENT_SEQUENCE:
            raise ValueError("sequence_number is outside the approved bound")
        if not isinstance(self.event_kind, AgentEventKind):
            raise TypeError("event_kind must be an AgentEventKind")
        if not isinstance(self.task_id, AgentTaskId):
            raise TypeError("task_id must be an AgentTaskId")
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported event schema version")
        if not isinstance(self.timestamp_utc, datetime) or self.timestamp_utc.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        object.__setattr__(self, "timestamp_utc", self.timestamp_utc.astimezone(timezone.utc))
        if self.step_index is not None:
            if isinstance(self.step_index, bool) or not isinstance(self.step_index, int):
                raise TypeError("step_index must be an integer")
            if not 0 <= self.step_index <= MAX_EVENT_STEP_INDEX:
                raise ValueError("step_index is outside the approved bound")
        if self.tool_name is not None and not isinstance(self.tool_name, ToolName):
            raise TypeError("tool_name must be a ToolName")
        if self.tool_contract_version is not None:
            object.__setattr__(
                self,
                "tool_contract_version",
                _safe_identifier(self.tool_contract_version, "tool_contract_version"),
            )
        if self.lifecycle_state is not None and not isinstance(
            self.lifecycle_state, ToolExecutionLifecycleState
        ):
            raise TypeError("lifecycle_state must be a ToolExecutionLifecycleState")
        if self.execution_outcome is not None and not isinstance(
            self.execution_outcome, ExecutionOutcome
        ):
            raise TypeError("execution_outcome must be an ExecutionOutcome")
        if self.error_code is not None and not isinstance(self.error_code, AgentErrorCode):
            raise TypeError("error_code must be an AgentErrorCode")
        if self.duration_ms is not None:
            if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, (int, float)):
                raise TypeError("duration_ms must be a finite non-negative number")
            normalized_duration = float(self.duration_ms)
            if (
                not isfinite(normalized_duration)
                or not 0 <= normalized_duration <= MAX_EVENT_DURATION_MS
            ):
                raise ValueError("duration_ms is outside the approved bound")
            object.__setattr__(self, "duration_ms", normalized_duration)
        if self.policy_version is None and self.policy_fingerprint is not None:
            raise ValueError("policy_version is required with policy_fingerprint")
        if self.policy_version is not None:
            object.__setattr__(
                self, "policy_version", _safe_identifier(self.policy_version, "policy_version")
            )
        if self.policy_fingerprint is not None:
            object.__setattr__(
                self, "policy_fingerprint", _sha256(self.policy_fingerprint, "policy_fingerprint")
            )
        if self.terminal_state is not None and not isinstance(self.terminal_state, AgentState):
            raise TypeError("terminal_state must be an AgentState")
        if self.terminal_result_kind is not None and not isinstance(
            self.terminal_result_kind, TerminalResultKind
        ):
            raise TypeError("terminal_result_kind must be a TerminalResultKind")
        self._validate_kind_contract()

    def _validate_kind_contract(self) -> None:
        kind = self.event_kind
        if kind is AgentEventKind.RUNTIME_POLICY_VALIDATED:
            if self.policy_version is None or self.policy_fingerprint is None:
                raise ValueError("policy validation event requires effective policy identity")
        if (
            kind
            in {
                AgentEventKind.PROPOSAL_ACCEPTED,
                AgentEventKind.PROPOSAL_REJECTED,
            }
            and self.step_index is None
        ):
            raise ValueError("proposal event requires step_index")
        if kind is AgentEventKind.PROPOSAL_REJECTED and self.error_code is None:
            raise ValueError("rejected proposal event requires a safe error code")
        if kind is AgentEventKind.TOOL_STARTED:
            if self.tool_name is None or self.tool_contract_version is None:
                raise ValueError("tool start event requires trusted tool identity")
            if (
                self.step_index is None
                or self.lifecycle_state is not ToolExecutionLifecycleState.RUNNING
            ):
                raise ValueError("tool start event lifecycle is invalid")
        if kind is AgentEventKind.TOOL_COMPLETED:
            self._require_tool_outcome(
                ToolExecutionLifecycleState.COMPLETED, ExecutionOutcome.KNOWN_SUCCESS
            )
        if kind is AgentEventKind.TOOL_FAILED:
            self._require_tool_outcome(
                ToolExecutionLifecycleState.FAILED, ExecutionOutcome.KNOWN_FAILURE
            )
            if self.error_code is None:
                raise ValueError("failed tool event requires a safe error code")
        if kind is AgentEventKind.TOOL_DEADLINE_EXCEEDED:
            self._require_tool_outcome(
                ToolExecutionLifecycleState.DEADLINE_EXCEEDED, ExecutionOutcome.UNKNOWN_OUTCOME
            )
            if self.error_code is not AgentErrorCode.TOOL_TIMEOUT:
                raise ValueError("deadline event requires TOOL_TIMEOUT")
        if kind is AgentEventKind.TOOL_OUTCOME_UNKNOWN:
            if self.tool_name is None or self.lifecycle_state not in {
                ToolExecutionLifecycleState.UNKNOWN,
                ToolExecutionLifecycleState.CANCELLATION_REQUESTED,
            }:
                raise ValueError("unknown-outcome event lifecycle is invalid")
            if self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME:
                raise ValueError("unknown-outcome event requires UNKNOWN_OUTCOME")
        if kind in {
            AgentEventKind.TERMINAL_RESULT_EMITTED,
            AgentEventKind.TASK_FINISHED,
        }:
            if self.terminal_state not in {
                AgentState.COMPLETED,
                AgentState.FAILED,
                AgentState.CANCELLED,
            }:
                raise ValueError("terminal event requires a terminal state")
            if self.terminal_result_kind is None:
                raise ValueError("terminal event requires a result category")

    def _require_tool_outcome(
        self, lifecycle_state: ToolExecutionLifecycleState, outcome: ExecutionOutcome
    ) -> None:
        if self.tool_name is None or self.tool_contract_version is None:
            raise ValueError("tool event requires trusted tool identity")
        if self.lifecycle_state is not lifecycle_state or self.execution_outcome is not outcome:
            raise ValueError("tool event lifecycle/outcome is inconsistent")

    def to_dict(self) -> dict[str, object | None]:
        """Serialize only the explicit safe event fields."""

        return {
            "duration_ms": self.duration_ms,
            "error_code": self.error_code.value if self.error_code is not None else None,
            "event_kind": self.event_kind.value,
            "execution_outcome": (
                self.execution_outcome.value if self.execution_outcome is not None else None
            ),
            "lifecycle_state": (
                self.lifecycle_state.value if self.lifecycle_state is not None else None
            ),
            "policy_fingerprint": self.policy_fingerprint,
            "policy_version": self.policy_version,
            "run_id": self.run_id.value,
            "schema_version": self.schema_version,
            "sequence_number": self.sequence_number,
            "step_index": self.step_index,
            "task_id": self.task_id.value,
            "terminal_result_kind": (
                self.terminal_result_kind.value if self.terminal_result_kind is not None else None
            ),
            "terminal_state": self.terminal_state.value
            if self.terminal_state is not None
            else None,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "tool_contract_version": self.tool_contract_version,
            "tool_name": self.tool_name.value if self.tool_name is not None else None,
        }

    def as_dict(self) -> dict[str, object | None]:
        return self.to_dict()


class AgentEventSink(Protocol):
    """Trusted application-owned destination for non-durable agent events."""

    def emit(self, event: AgentEvent) -> None: ...


class NullAgentEventSink:
    """Default no-op sink; event delivery is never an execution authority."""

    def emit(self, event: AgentEvent) -> None:
        if not isinstance(event, AgentEvent):
            raise TypeError("event must be an AgentEvent")


class InMemoryAgentEventSink:
    """Test/local collector with trusted per-run sequence checks."""

    def __init__(self) -> None:
        self._events: list[AgentEvent] = []
        self._last_sequences: dict[AgentRunId, int] = {}

    def emit(self, event: AgentEvent) -> None:
        if not isinstance(event, AgentEvent):
            raise TypeError("event must be an AgentEvent")
        previous = self._last_sequences.get(event.run_id)
        if previous is not None and event.sequence_number <= previous:
            raise ValueError("event sequence must be monotonic")
        self._last_sequences[event.run_id] = event.sequence_number
        self._events.append(event)

    @property
    def events(self) -> tuple[AgentEvent, ...]:
        return tuple(self._events)


__all__ = [
    "AgentEvent",
    "AgentEventKind",
    "AgentEventSink",
    "AgentRunId",
    "EVENT_SCHEMA_VERSION",
    "InMemoryAgentEventSink",
    "NullAgentEventSink",
]
