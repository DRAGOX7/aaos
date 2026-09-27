"""Trusted Phase 5 value contracts.

This module intentionally contains no model, network, persistence, or tool
execution code. Values crossing the future model boundary are represented as
untrusted proposal types; trusted application values are separate and
validated before they can be used by later slices.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias

from aaos.rag import CourseScope, GlobalScope, HistoricalScope, RagScope

MAX_AGENT_TEXT_CHARACTERS = 16_384
MAX_AGENT_ID_CHARACTERS = 128
MAX_TOOL_NAME_CHARACTERS = 64
MAX_TIMEOUT_SECONDS = 86_400
_EMPTY_FINGERPRINT = "0" * 64
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_OBSERVATION_KIND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class AgentState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    TOOL_PENDING = "TOOL_PENDING"
    TOOL_RUNNING = "TOOL_RUNNING"
    OBSERVING = "OBSERVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolName(StrEnum):
    RAG_QUERY = "rag_query"
    ACADEMIC_LIST_COURSES = "academic.list_courses"
    ACADEMIC_GET_COURSE = "academic.get_course"
    ACADEMIC_LIST_ASSIGNMENTS = "academic.list_assignments"
    ACADEMIC_GET_ASSIGNMENT = "academic.get_assignment"
    ACADEMIC_LIST_DOCUMENTS = "academic.list_documents"
    RAG_ANSWER = "rag.answer"
    STUDY_TASK_LIST = "study_task.list"
    STUDY_TASK_GET = "study_task.get"
    STUDY_TASK_CREATE = "study_task.create"
    STUDY_TASK_UPDATE = "study_task.update"
    STUDY_TASK_COMPLETE = "study_task.complete"
    READ_TASK_STATE = "read_task_state"
    WRITE_TASK_STATE = "write_task_state"
    COMPLETE_TASK = "complete_task"


class PermissionClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOCAL_STATE_WRITE = "LOCAL_STATE_WRITE"
    TERMINAL = "TERMINAL"


class PermissionDecisionStatus(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class TerminalResultKind(StrEnum):
    """Trusted discriminant for an agent's final application result."""

    GROUNDED_RAG_RESPONSE = "GROUNDED_RAG_RESPONSE"
    CONTROL_OR_CONVERSATIONAL_TERMINAL = "CONTROL_OR_CONVERSATIONAL_TERMINAL"


class ToolExecutionLifecycleState(StrEnum):
    """Trusted state of one bounded tool operation."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    TERMINATED = "TERMINATED"
    UNKNOWN = "UNKNOWN"


class ExecutionOutcome(StrEnum):
    KNOWN_SUCCESS = "KNOWN_SUCCESS"
    KNOWN_FAILURE = "KNOWN_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class AgentErrorCode(StrEnum):
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    INVALID_LIMIT = "INVALID_LIMIT"
    INVALID_TOOL_NAME = "INVALID_TOOL_NAME"
    INVALID_TOOL_ARGUMENTS = "INVALID_TOOL_ARGUMENTS"
    TASK_ALREADY_TERMINAL = "TASK_ALREADY_TERMINAL"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    STEP_LIMIT_EXCEEDED = "STEP_LIMIT_EXCEEDED"
    LOOP_DETECTED = "LOOP_DETECTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"
    TOOL_EXECUTION_IN_PROGRESS = "TOOL_EXECUTION_IN_PROGRESS"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_NOT_EXECUTABLE = "TOOL_NOT_EXECUTABLE"
    DUPLICATE_TOOL_REGISTRATION = "DUPLICATE_TOOL_REGISTRATION"
    DYNAMIC_TOOL_REGISTRATION = "DYNAMIC_TOOL_REGISTRATION"
    INVALID_TOOL_DEFINITION = "INVALID_TOOL_DEFINITION"
    INCONSISTENT_RUNTIME_POLICY = "INCONSISTENT_RUNTIME_POLICY"
    OBSERVATION_HISTORY_LIMIT_EXCEEDED = "OBSERVATION_HISTORY_LIMIT_EXCEEDED"
    HANDLER_EXECUTION_FAILED = "HANDLER_EXECUTION_FAILED"
    UNKNOWN_PERMISSION = "UNKNOWN_PERMISSION"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    OBSERVATION_SANITIZATION_FAILED = "OBSERVATION_SANITIZATION_FAILED"
    RAG_QUERY_FAILED = "RAG_QUERY_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    INTERNAL_AGENT_FAILURE = "INTERNAL_AGENT_FAILURE"
    SCOPE_DENIED = "SCOPE_DENIED"
    SCOPE_GRANTED = "SCOPE_GRANTED"
    INVALID_GROUNDED_REFERENCE = "INVALID_GROUNDED_REFERENCE"
    COURSE_NOT_FOUND = "COURSE_NOT_FOUND"
    AMBIGUOUS_COURSE = "AMBIGUOUS_COURSE"
    ASSIGNMENT_NOT_FOUND = "ASSIGNMENT_NOT_FOUND"
    AMBIGUOUS_ASSIGNMENT = "AMBIGUOUS_ASSIGNMENT"
    AMBIGUOUS_STUDY_TASK = "AMBIGUOUS_STUDY_TASK"
    STUDY_TASK_NOT_FOUND = "STUDY_TASK_NOT_FOUND"
    STUDY_TASK_IDEMPOTENCY_CONFLICT = "STUDY_TASK_IDEMPOTENCY_CONFLICT"
    NO_MATCHING_DOCUMENT = "NO_MATCHING_DOCUMENT"
    AMBIGUOUS_DOCUMENT = "AMBIGUOUS_DOCUMENT"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    INVALID_TOOL_OUTPUT = "INVALID_TOOL_OUTPUT"
    REQUEST_DEADLINE_EXCEEDED = "REQUEST_DEADLINE_EXCEEDED"


AgentFailureCode = AgentErrorCode
ToolErrorCode = AgentErrorCode


def _nonblank_text(value: object, name: str, *, limit: int = MAX_AGENT_TEXT_CHARACTERS) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds the maximum length")
    return normalized


def _safe_identifier(value: object, name: str, *, limit: int = MAX_AGENT_ID_CHARACTERS) -> str:
    normalized = _nonblank_text(value, name, limit=limit)
    if len(normalized) > MAX_AGENT_ID_CHARACTERS or not _SAFE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a safe logical identifier")
    return normalized


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite_timeout(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0 or numeric > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"{name} must be a finite positive number")
    return numeric


def _sha256(value: object, name: str) -> str:
    normalized = _nonblank_text(value, name, limit=64)
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hexadecimal digest")
    return normalized


def _freeze_json(value: object, path: str = "value") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError(f"{path} object keys must be non-blank strings")
            if len(key) > MAX_TOOL_NAME_CHARACTERS:
                raise ValueError(f"{path} object keys are too long")
            frozen[key] = _freeze_json(nested, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(nested, f"{path}[]") for nested in value)
    raise TypeError(f"{path} contains a non-JSON value")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _jsonable(value: object) -> object:
    if isinstance(value, StrEnum):
        return str(value)
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_jsonable(nested) for nested in value]
    if isinstance(value, tuple):
        return [_jsonable(nested) for nested in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not isfinite(value):
            raise TypeError("non-finite numbers are not JSON-compatible")
        return value
    raise TypeError("contract value is not JSON-compatible")


def canonical_json(value: object) -> str:
    """Serialize a contract using deterministic, JSON-compatible encoding."""

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_contract(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentTaskId:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _safe_identifier(self.value, "task_id"))

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TrustedTaskScope:
    """Application-owned Phase 4 scope grant for one agent task.

    The wrapped scope is immutable and is never constructed from model text.
    Historical grants are canonicalized and reject duplicate version ids so
    authorization can use exact identity comparison.
    """

    scope: RagScope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, (CourseScope, GlobalScope, HistoricalScope)):
            raise TypeError("scope must be a supported Phase 4 scope")
        if isinstance(self.scope, HistoricalScope):
            ids = self.scope.document_version_ids
            if len(set(ids)) != len(ids):
                raise ValueError("trusted historical scope must not contain duplicate versions")
            object.__setattr__(self, "scope", HistoricalScope(tuple(sorted(ids))))

    def to_dict(self) -> dict[str, object]:
        if isinstance(self.scope, CourseScope):
            return {"kind": "course", "course_id": self.scope.course_id}
        if isinstance(self.scope, GlobalScope):
            return {"kind": "global"}
        return {
            "kind": "historical",
            "document_version_ids": list(self.scope.document_version_ids),
        }


@dataclass(frozen=True, slots=True)
class AgentTask:
    task_id: AgentTaskId
    request_text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    initial_scope: TrustedTaskScope | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    state: AgentState = AgentState.CREATED

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, AgentTaskId):
            raise TypeError("task_id must be an AgentTaskId")
        object.__setattr__(self, "request_text", _nonblank_text(self.request_text, "request_text"))
        if self.initial_scope is not None and not isinstance(self.initial_scope, TrustedTaskScope):
            raise TypeError("initial_scope must be a TrustedTaskScope")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if not isinstance(self.metadata, tuple):
            raise TypeError("metadata must be a tuple")
        seen: set[str] = set()
        normalized: list[tuple[str, str]] = []
        for item in self.metadata:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("metadata entries must be key/value tuples")
            key = _safe_identifier(item[0], "metadata key", limit=64)
            value = _nonblank_text(item[1], "metadata value")
            if key in seen:
                raise ValueError("metadata keys must be unique")
            seen.add(key)
            normalized.append((key, value))
        object.__setattr__(self, "metadata", tuple(sorted(normalized)))
        if self.state is not AgentState.CREATED:
            raise ValueError("AgentTask state must start at CREATED")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "task_id": self.task_id.to_dict(),
            "request_text": self.request_text,
            "created_at": self.created_at.isoformat(),
            "metadata": {key: value for key, value in self.metadata},
            "state": self.state.value,
        }
        if self.initial_scope is not None:
            result["initial_scope"] = self.initial_scope.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_agent_steps: int = 8
    max_structured_proposal_repairs: int = 1
    max_identical_tool_call_repetitions: int = 2
    model_proposal_timeout_seconds: float = 60
    rag_query_timeout_seconds: float = 120
    read_task_state_timeout_seconds: float = 5
    write_task_state_timeout_seconds: float = 5
    complete_task_timeout_seconds: float = 5

    def __post_init__(self) -> None:
        _positive_int(self.max_agent_steps, "max_agent_steps")
        _positive_int(self.max_structured_proposal_repairs, "max_structured_proposal_repairs")
        _positive_int(
            self.max_identical_tool_call_repetitions, "max_identical_tool_call_repetitions"
        )
        for name in (
            "model_proposal_timeout_seconds",
            "rag_query_timeout_seconds",
            "read_task_state_timeout_seconds",
            "write_task_state_timeout_seconds",
            "complete_task_timeout_seconds",
        ):
            _finite_timeout(getattr(self, name), name)

    def to_dict(self) -> dict[str, object]:
        return {
            "max_agent_steps": self.max_agent_steps,
            "max_structured_proposal_repairs": self.max_structured_proposal_repairs,
            "max_identical_tool_call_repetitions": self.max_identical_tool_call_repetitions,
            "model_proposal_timeout_seconds": self.model_proposal_timeout_seconds,
            "rag_query_timeout_seconds": self.rag_query_timeout_seconds,
            "read_task_state_timeout_seconds": self.read_task_state_timeout_seconds,
            "write_task_state_timeout_seconds": self.write_task_state_timeout_seconds,
            "complete_task_timeout_seconds": self.complete_task_timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ToolArguments:
    """Raw, unvalidated model arguments.

    Slice 1 deliberately has no constructor for trusted executable arguments.
    Slice 2 must validate these values against a registered tool schema before
    creating any executable call representation.
    """

    values: tuple[tuple[str, object], ...]
    validated: bool = False

    def __post_init__(self) -> None:
        if self.validated:
            raise ValueError("raw ToolArguments cannot be marked validated")
        if not isinstance(self.values, tuple):
            raise TypeError("ToolArguments values must be a tuple")
        normalized: list[tuple[str, object]] = []
        seen: set[str] = set()
        for item in self.values:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("ToolArguments entries must be key/value tuples")
            key = _safe_identifier(item[0], "argument key", limit=MAX_TOOL_NAME_CHARACTERS)
            if key in seen:
                raise ValueError("ToolArguments keys must be unique")
            seen.add(key)
            normalized.append((key, _freeze_json(item[1], f"arguments.{key}")))
        object.__setattr__(self, "values", tuple(sorted(normalized)))

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> ToolArguments:
        if not isinstance(values, Mapping):
            raise TypeError("ToolArguments require a mapping")
        return cls(tuple((key, value) for key, value in values.items()))

    def to_mapping(self) -> dict[str, object]:
        return {key: _thaw_json(value) for key, value in self.values}

    def to_dict(self) -> dict[str, object]:
        return {"arguments": self.to_mapping(), "validated": False}


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Untrusted proposed call; it is not executable in Slice 1."""

    name: ToolName
    arguments: ToolArguments
    call_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, ToolName):
            raise TypeError("name must be a ToolName")
        if not isinstance(self.arguments, ToolArguments) or self.arguments.validated:
            raise TypeError("arguments must be raw ToolArguments")
        if self.call_id is not None:
            object.__setattr__(self, "call_id", _safe_identifier(self.call_id, "call_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name.value,
            "arguments": self.arguments.to_mapping(),
            **({"call_id": self.call_id} if self.call_id is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class AgentProposal:
    """Strictly parsed but still untrusted model proposal."""

    tool_call: ToolCall | None = None
    terminal_text: str | None = None
    proposal_id: str | None = None
    grounded_result_ref: str | None = None

    def __post_init__(self) -> None:
        actions = sum(
            value is not None
            for value in (self.tool_call, self.terminal_text, self.grounded_result_ref)
        )
        if actions != 1:
            raise ValueError("proposal must contain exactly one action")
        if self.tool_call is not None and not isinstance(self.tool_call, ToolCall):
            raise TypeError("tool_call must be a ToolCall")
        if self.terminal_text is not None:
            object.__setattr__(
                self, "terminal_text", _nonblank_text(self.terminal_text, "terminal_text")
            )
        if self.proposal_id is not None:
            object.__setattr__(
                self, "proposal_id", _safe_identifier(self.proposal_id, "proposal_id")
            )
        if self.grounded_result_ref is not None:
            object.__setattr__(
                self,
                "grounded_result_ref",
                _safe_identifier(self.grounded_result_ref, "grounded_result_ref"),
            )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> AgentProposal:
        if not isinstance(payload, Mapping):
            raise TypeError("proposal must be an object")
        allowed = {"proposal_id", "tool_call", "terminal_text", "grounded_result_ref"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError("proposal contains unknown fields")
        proposal_id = payload.get("proposal_id")
        if proposal_id is not None and not isinstance(proposal_id, str):
            raise TypeError("proposal_id must be a string")
        terminal_text = payload.get("terminal_text")
        if terminal_text is not None and not isinstance(terminal_text, str):
            raise TypeError("terminal_text must be a string")
        grounded_result_ref = payload.get("grounded_result_ref")
        if grounded_result_ref is not None and not isinstance(grounded_result_ref, str):
            raise TypeError("grounded_result_ref must be a string")
        raw_call = payload.get("tool_call")
        call: ToolCall | None = None
        if raw_call is not None:
            if not isinstance(raw_call, Mapping):
                raise TypeError("tool_call must be an object")
            if set(raw_call) not in (
                {"name", "arguments"},
                {"name", "arguments", "call_id"},
            ):
                raise ValueError("tool_call contains unknown or missing fields")
            raw_name = raw_call.get("name")
            raw_arguments = raw_call.get("arguments")
            if not isinstance(raw_name, str):
                raise TypeError("tool_call name must be a string")
            try:
                name = ToolName(raw_name)
            except ValueError as exc:
                raise ValueError("tool_call contains an unsupported tool name") from exc
            if not isinstance(raw_arguments, Mapping):
                raise TypeError("tool_call arguments must be an object")
            call_id = raw_call.get("call_id")
            if call_id is not None and not isinstance(call_id, str):
                raise TypeError("call_id must be a string")
            call = ToolCall(ToolName(name), ToolArguments.from_mapping(raw_arguments), call_id)
        return cls(
            tool_call=call,
            terminal_text=terminal_text,
            proposal_id=proposal_id,
            grounded_result_ref=grounded_result_ref,
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.proposal_id is not None:
            result["proposal_id"] = self.proposal_id
        if self.tool_call is not None:
            result["tool_call"] = self.tool_call.to_dict()
        if self.terminal_text is not None:
            result["terminal_text"] = self.terminal_text
        if self.grounded_result_ref is not None:
            result["grounded_result_ref"] = self.grounded_result_ref
        return result


@dataclass(frozen=True, slots=True)
class AgentStep:
    step_number: int
    from_state: AgentState
    to_state: AgentState
    proposal_fingerprint: str = _EMPTY_FINGERPRINT
    tool_call_fingerprint: str | None = None
    outcome_code: AgentErrorCode | None = None

    def __post_init__(self) -> None:
        _positive_int(self.step_number, "step_number")
        if not isinstance(self.from_state, AgentState) or not isinstance(self.to_state, AgentState):
            raise TypeError("step states must be AgentState values")
        object.__setattr__(
            self,
            "proposal_fingerprint",
            _sha256(self.proposal_fingerprint, "proposal_fingerprint"),
        )
        if self.tool_call_fingerprint is not None:
            object.__setattr__(
                self,
                "tool_call_fingerprint",
                _sha256(self.tool_call_fingerprint, "tool_call_fingerprint"),
            )
        if self.outcome_code is not None and not isinstance(self.outcome_code, AgentErrorCode):
            raise TypeError("outcome_code must be an AgentErrorCode")

    def to_dict(self) -> dict[str, object]:
        return {
            "from_state": self.from_state.value,
            "outcome_code": self.outcome_code.value if self.outcome_code else None,
            "proposal_fingerprint": self.proposal_fingerprint,
            "step_number": self.step_number,
            "to_state": self.to_state.value,
            "tool_call_fingerprint": self.tool_call_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ToolError:
    code: AgentErrorCode
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, AgentErrorCode):
            raise TypeError("code must be an AgentErrorCode")
        object.__setattr__(self, "message", _nonblank_text(self.message, "message"))
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code.value, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    output: object | None = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise TypeError("ok must be a boolean")
        if self.ok:
            if self.error is not None:
                raise ValueError("successful result cannot contain an error")
            object.__setattr__(self, "output", _freeze_json(self.output, "output"))
        else:
            if not isinstance(self.error, ToolError):
                raise ValueError("failed result requires a ToolError")
            if self.output is not None:
                raise ValueError("failed result cannot contain output")

    @classmethod
    def success(cls, output: object | None = None) -> ToolResult:
        return cls(ok=True, output=output)

    @classmethod
    def failure(cls, error: ToolError) -> ToolResult:
        return cls(ok=False, error=error)

    def to_dict(self) -> dict[str, object]:
        return {
            "error": self.error.to_dict() if self.error else None,
            "ok": self.ok,
            "output": _thaw_json(self.output),
        }


@dataclass(frozen=True, slots=True)
class ToolObservation:
    kind: str
    status: str
    text: str | None = None
    fields: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _SAFE_OBSERVATION_KIND.fullmatch(self.kind):
            raise ValueError("kind must be a safe observation identifier")
        object.__setattr__(self, "status", _safe_identifier(self.status, "status", limit=64))
        if self.text is not None:
            object.__setattr__(self, "text", _nonblank_text(self.text, "text"))
        if not isinstance(self.fields, tuple):
            raise TypeError("fields must be a tuple")
        normalized: list[tuple[str, object]] = []
        seen: set[str] = set()
        for item in self.fields:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("observation fields must be key/value tuples")
            key = _safe_identifier(item[0], "field key", limit=64)
            if key in seen:
                raise ValueError("observation field keys must be unique")
            seen.add(key)
            normalized.append((key, _freeze_json(item[1], f"fields.{key}")))
        object.__setattr__(self, "fields", tuple(sorted(normalized)))

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": {key: _thaw_json(value) for key, value in self.fields},
            "kind": self.kind,
            "status": self.status,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    required_permission: PermissionClass
    reason_code: AgentErrorCode
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a boolean")
        if not isinstance(self.required_permission, PermissionClass):
            raise TypeError("required_permission must be a PermissionClass")
        if not isinstance(self.reason_code, AgentErrorCode):
            raise TypeError("reason_code must be an AgentErrorCode")
        object.__setattr__(
            self, "policy_version", _safe_identifier(self.policy_version, "policy_version")
        )

    @property
    def status(self) -> PermissionDecisionStatus:
        return PermissionDecisionStatus.ALLOW if self.allowed else PermissionDecisionStatus.DENY

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code.value,
            "required_permission": self.required_permission.value,
            "status": self.status.value,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class AgentFailure:
    code: AgentErrorCode
    message: str
    stage: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, AgentErrorCode):
            raise TypeError("code must be an AgentErrorCode")
        object.__setattr__(self, "message", _nonblank_text(self.message, "message"))
        object.__setattr__(self, "stage", _safe_identifier(self.stage, "stage", limit=64))
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "stage": self.stage,
        }


@dataclass(frozen=True, slots=True)
class ToolExecutionLifecycle:
    """Safe snapshot separating request wait state from operation outcome."""

    state: ToolExecutionLifecycleState
    request_deadline_exceeded: bool
    cancellation_requested: bool
    operation_terminated: bool | None
    execution_outcome: ExecutionOutcome
    execution_outcome_known: bool
    resource_cleanup_requested: bool = False
    resource_cleanup_complete: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ToolExecutionLifecycleState):
            raise TypeError("state must be a ToolExecutionLifecycleState")
        for name in ("request_deadline_exceeded", "cancellation_requested"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.operation_terminated is not None and not isinstance(
            self.operation_terminated, bool
        ):
            raise TypeError("operation_terminated must be a boolean or None")
        if not isinstance(self.execution_outcome, ExecutionOutcome):
            raise TypeError("execution_outcome must be an ExecutionOutcome")
        if not isinstance(self.execution_outcome_known, bool):
            raise TypeError("execution_outcome_known must be a boolean")
        if not isinstance(self.resource_cleanup_requested, bool):
            raise TypeError("resource_cleanup_requested must be a boolean")
        if self.resource_cleanup_complete is not None and not isinstance(
            self.resource_cleanup_complete, bool
        ):
            raise TypeError("resource_cleanup_complete must be a boolean or None")
        if self.resource_cleanup_complete is True and self.operation_terminated is not True:
            raise ValueError("cleanup cannot be complete before operation termination")
        if self.execution_outcome_known is not (
            self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME
        ):
            raise ValueError("execution outcome known flag does not match outcome")
        if (
            self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME
            and self.operation_terminated is not True
        ):
            raise ValueError("known outcome requires operation termination")
        if self.state is ToolExecutionLifecycleState.RUNNING:
            if self.request_deadline_exceeded or self.cancellation_requested:
                raise ValueError("running state cannot have a terminal request flag")
            if self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME:
                raise ValueError("running state requires an unknown outcome")
            if self.operation_terminated is True:
                raise ValueError("running state cannot claim operation termination")
        if self.state is ToolExecutionLifecycleState.COMPLETED:
            if self.request_deadline_exceeded or self.cancellation_requested:
                raise ValueError("completed state cannot have a terminal request flag")
            if self.execution_outcome is not ExecutionOutcome.KNOWN_SUCCESS:
                raise ValueError("completed state requires known success")
            if self.operation_terminated is not True:
                raise ValueError("completed state requires operation termination")
        if self.state is ToolExecutionLifecycleState.FAILED:
            if self.request_deadline_exceeded or self.cancellation_requested:
                raise ValueError("failed state cannot have a terminal request flag")
            if self.execution_outcome is not ExecutionOutcome.KNOWN_FAILURE:
                raise ValueError("failed state requires known failure")
            if self.operation_terminated is not True:
                raise ValueError("failed state requires operation termination")
        if self.state is ToolExecutionLifecycleState.DEADLINE_EXCEEDED:
            if not self.request_deadline_exceeded:
                raise ValueError("deadline state requires an exceeded request deadline")
            if self.cancellation_requested:
                raise ValueError("deadline state cannot claim cancellation")
            if self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME:
                raise ValueError("deadline state requires an unknown outcome")
            if self.operation_terminated is True:
                raise ValueError("deadline state cannot claim operation termination")
        if self.state is ToolExecutionLifecycleState.CANCELLATION_REQUESTED:
            if not self.cancellation_requested:
                raise ValueError("cancellation state requires a cancellation request")
            if self.request_deadline_exceeded:
                raise ValueError("cancellation state cannot claim a deadline")
            if self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME:
                raise ValueError("cancellation state requires an unknown outcome")
            if self.operation_terminated is True:
                raise ValueError("cancellation state cannot claim operation termination")
        if self.state is ToolExecutionLifecycleState.UNKNOWN:
            if self.request_deadline_exceeded or self.cancellation_requested:
                raise ValueError("unknown state cannot have a terminal request flag")
            if self.execution_outcome is not ExecutionOutcome.UNKNOWN_OUTCOME:
                raise ValueError("unknown state requires an unknown outcome")
            if self.operation_terminated is True:
                raise ValueError("unknown state cannot claim operation termination")
        if self.state is ToolExecutionLifecycleState.TERMINATED:
            if self.request_deadline_exceeded or self.cancellation_requested:
                raise ValueError("terminated state cannot have a terminal request flag")
            if self.operation_terminated is not True:
                raise ValueError("terminated state requires operation termination")
        if self.execution_outcome is ExecutionOutcome.UNKNOWN_OUTCOME and self.operation_terminated:
            raise ValueError("unknown outcome cannot claim operation termination")

    def to_dict(self) -> dict[str, object]:
        return {
            "cancellation_requested": self.cancellation_requested,
            "execution_outcome": self.execution_outcome.value,
            "execution_outcome_known": self.execution_outcome_known,
            "operation_terminated": self.operation_terminated,
            "request_deadline_exceeded": self.request_deadline_exceeded,
            "resource_cleanup_complete": self.resource_cleanup_complete,
            "resource_cleanup_requested": self.resource_cleanup_requested,
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class GroundedRagResponse:
    """Trusted, task-local projection of one validated Phase 4 RAG result.

    The payload is created by trusted application code from a successful
    ``rag_query`` handler result.  It is never parsed from model terminal
    text, and the task/ref identity prevents stale or cross-task references.
    """

    task_id: AgentTaskId
    result_ref: str
    payload: Mapping[str, object]

    _PAYLOAD_FIELDS = frozenset(
        {
            "answer_text",
            "citations",
            "content_revision",
            "generation_id",
            "language",
            "rendered_text",
            "status",
        }
    )
    _STATUSES = frozenset({"ANSWER", "PARTIAL_ANSWER", "INSUFFICIENT_EVIDENCE"})

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, AgentTaskId):
            raise TypeError("task_id must be an AgentTaskId")
        object.__setattr__(self, "result_ref", _safe_identifier(self.result_ref, "result_ref"))
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        frozen_payload = _freeze_json(self.payload, "payload")
        if not isinstance(frozen_payload, Mapping):
            raise TypeError("payload must be a JSON object")
        values = dict(frozen_payload)
        if set(values) != self._PAYLOAD_FIELDS:
            raise ValueError("grounded RAG payload fields are invalid")
        status = values["status"]
        if not isinstance(status, str) or status not in self._STATUSES:
            raise ValueError("grounded RAG status is invalid")
        for field_name in ("answer_text", "rendered_text"):
            _nonblank_text(values[field_name], f"payload.{field_name}")
        language = values["language"]
        if not isinstance(language, str) or language.casefold() not in {"en", "ar"}:
            raise ValueError("grounded RAG language is invalid")
        _positive_int(values["generation_id"], "payload.generation_id")
        _nonnegative_int(values["content_revision"], "payload.content_revision")
        citations = values["citations"]
        if not isinstance(citations, tuple):
            raise ValueError("grounded RAG citations must be a tuple")

        page_fields = {
            "claim_id",
            "document_version_id",
            "document_title",
            "page_start",
            "page_end",
        }
        structure_fields = {
            "claim_id",
            "document_version_id",
            "document_title",
            "unit_start",
            "unit_end",
            "heading",
            "table_index",
            "row_index",
            "column_index",
        }

        for citation in citations:
            if not isinstance(citation, Mapping):
                raise ValueError("grounded RAG citation is invalid")

            citation_fields = set(citation)

            _nonblank_text(
                citation.get("claim_id"),
                "citation.claim_id",
            )
            _nonblank_text(
                citation.get("document_title"),
                "citation.document_title",
            )
            _positive_int(
                citation.get("document_version_id"),
                "citation.document_version_id",
            )

            if citation_fields == page_fields:
                _positive_int(
                    citation["page_start"],
                    "citation.page_start",
                )
                _positive_int(
                    citation["page_end"],
                    "citation.page_end",
                )
                if citation["page_end"] < citation["page_start"]:
                    raise ValueError("grounded RAG citation page range is invalid")
                continue

            if citation_fields != structure_fields:
                raise ValueError("grounded RAG citation is invalid")

            _positive_int(
                citation["unit_start"],
                "citation.unit_start",
            )
            _positive_int(
                citation["unit_end"],
                "citation.unit_end",
            )
            if citation["unit_end"] < citation["unit_start"]:
                raise ValueError("grounded RAG citation unit range is invalid")

            heading = citation["heading"]
            if heading is not None:
                _nonblank_text(
                    heading,
                    "citation.heading",
                )

            coordinates = (
                citation["table_index"],
                citation["row_index"],
                citation["column_index"],
            )

            if all(value is None for value in coordinates):
                continue

            if any(value is None for value in coordinates):
                raise ValueError(
                    "grounded RAG structural citation coordinates must be all present or all absent"
                )

            for coordinate_field, value in zip(
                (
                    "table_index",
                    "row_index",
                    "column_index",
                ),
                coordinates,
                strict=True,
            ):
                _nonnegative_int(
                    value,
                    f"citation.{coordinate_field}",
                )

        if status == "INSUFFICIENT_EVIDENCE" and citations:
            raise ValueError("grounded RAG refusal cannot contain citations")
        if status != "INSUFFICIENT_EVIDENCE" and not citations:
            raise ValueError("grounded RAG supported response requires citations")
        object.__setattr__(self, "payload", frozen_payload)

    @classmethod
    def from_tool_result(
        cls, task_id: AgentTaskId, result_ref: str, result: ToolResult
    ) -> "GroundedRagResponse":
        if (
            not isinstance(result, ToolResult)
            or not result.ok
            or not isinstance(result.output, Mapping)
        ):
            raise ValueError("result is not a successful RAG payload")
        return cls(task_id, result_ref, result.output)

    def to_dict(self) -> dict[str, object]:
        return {
            "payload": _thaw_json(self.payload),
            "result_ref": self.result_ref,
            "task_id": self.task_id.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentTerminalResult:
    task_id: AgentTaskId
    state: AgentState
    step_count: int
    result_text: str | None = None
    failure: AgentFailure | None = None
    result_kind: TerminalResultKind = TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL
    grounded_rag_response: GroundedRagResponse | None = None
    tool_execution_lifecycle: ToolExecutionLifecycle | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, AgentTaskId):
            raise TypeError("task_id must be an AgentTaskId")
        if self.state not in {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}:
            raise ValueError("terminal result state must be terminal")
        _nonnegative_int(self.step_count, "step_count")
        if self.result_text is not None:
            object.__setattr__(self, "result_text", _nonblank_text(self.result_text, "result_text"))
        if self.failure is not None and not isinstance(self.failure, AgentFailure):
            raise TypeError("failure must be an AgentFailure")
        if self.tool_execution_lifecycle is not None and not isinstance(
            self.tool_execution_lifecycle, ToolExecutionLifecycle
        ):
            raise TypeError("tool_execution_lifecycle must be a ToolExecutionLifecycle")
        if self.state is AgentState.FAILED and self.failure is None:
            raise ValueError("failed result requires an AgentFailure")
        if not isinstance(self.result_kind, TerminalResultKind):
            raise TypeError("result_kind must be a TerminalResultKind")
        if self.result_kind is TerminalResultKind.GROUNDED_RAG_RESPONSE:
            if self.state is not AgentState.COMPLETED:
                raise ValueError("grounded response requires completed state")
            if self.failure is not None or self.grounded_rag_response is None:
                raise ValueError("grounded response cannot contain failure or be absent")
            if self.grounded_rag_response.task_id != self.task_id:
                raise ValueError("grounded response task identity does not match")
            rendered_text = self.grounded_rag_response.payload["rendered_text"]
            if not isinstance(rendered_text, str):
                raise ValueError("grounded response rendered text is invalid")
            if self.result_text is None:
                object.__setattr__(self, "result_text", rendered_text)
            elif self.result_text != rendered_text:
                raise ValueError("grounded result text must preserve Phase 4 rendered text")
        elif self.grounded_rag_response is not None:
            raise ValueError("control terminal cannot contain a grounded response")

    def to_dict(self) -> dict[str, object]:
        return {
            "failure": self.failure.to_dict() if self.failure else None,
            "grounded_rag_response": (
                self.grounded_rag_response.to_dict() if self.grounded_rag_response else None
            ),
            "result_kind": self.result_kind.value,
            "result_text": self.result_text,
            "state": self.state.value,
            "step_count": self.step_count,
            "task_id": self.task_id.to_dict(),
            "tool_execution_lifecycle": (
                self.tool_execution_lifecycle.to_dict()
                if self.tool_execution_lifecycle is not None
                else None
            ),
        }

    @property
    def grounded(self) -> bool:
        return self.result_kind is TerminalResultKind.GROUNDED_RAG_RESPONSE


__all__ = [
    "AgentErrorCode",
    "AgentFailure",
    "AgentFailureCode",
    "ExecutionOutcome",
    "GroundedRagResponse",
    "AgentLimits",
    "AgentProposal",
    "AgentState",
    "AgentStep",
    "AgentTask",
    "AgentTaskId",
    "AgentTerminalResult",
    "JSONValue",
    "PermissionClass",
    "PermissionDecision",
    "PermissionDecisionStatus",
    "TerminalResultKind",
    "ToolExecutionLifecycle",
    "ToolExecutionLifecycleState",
    "ToolArguments",
    "ToolCall",
    "ToolError",
    "ToolErrorCode",
    "ToolName",
    "ToolObservation",
    "ToolResult",
    "canonical_json",
    "fingerprint_contract",
]
