"""Trusted composition of the effective Phase 5 runtime policy.

The policy is assembled once by application code.  Component configuration is
treated as an input to composition, never as an authority that can override
the resulting immutable snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, cast

from .contracts import (
    MAX_AGENT_TEXT_CHARACTERS,
    AgentErrorCode,
    AgentLimits,
    ToolName,
    fingerprint_contract,
)

if TYPE_CHECKING:
    from .registry import ToolRegistry


MAX_AGENT_STEPS = 8
MAX_TOOL_CALLS = 4
USEFUL_RAG_CALLS_PER_REQUEST_MAX: Final[int] = 1
MAX_OBSERVATIONS = 8
MAX_OBSERVATION_SIZE = MAX_AGENT_TEXT_CHARACTERS
MAX_OBSERVATION_HISTORY_SIZE = MAX_AGENT_TEXT_CHARACTERS
MAX_POLICY_TIMEOUT_SECONDS = 86_400
MAX_MODEL_PROPOSAL_TIMEOUT_SECONDS = 60.0
_CONCEPTUAL_TOOL_TIMEOUTS: Mapping[str, float] = MappingProxyType(
    {
        "academic.get_assignment": 5.0,
        "academic.get_course": 5.0,
        "academic.list_assignments": 5.0,
        "academic.list_courses": 5.0,
        "academic.list_documents": 5.0,
        "study_task.list": 5.0,
        "study_task.get": 5.0,
        "study_task.create": 5.0,
        "study_task.update": 5.0,
        "study_task.complete": 5.0,
        "rag_query": 120.0,
        "rag.answer": 120.0,
        "read_task_state": 5.0,
        "write_task_state": 5.0,
        "complete_task": 5.0,
    }
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class RuntimePolicyCompositionError(ValueError):
    """Safe deterministic failure for an inconsistent runtime composition."""

    code = AgentErrorCode.INCONSISTENT_RUNTIME_POLICY

    def __init__(self, message: str = "runtime policy components are inconsistent") -> None:
        super().__init__(f"{self.code.value}: {message}")


def _positive_int(value: object, name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimePolicyCompositionError(f"{name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise RuntimePolicyCompositionError(f"{name} exceeds the approved bound")
    return value


def _bounded_observation_size(value: object, name: str) -> int:
    normalized = _positive_int(value, name)
    if normalized > MAX_OBSERVATION_SIZE:
        raise RuntimePolicyCompositionError(f"{name} exceeds the approved observation bound")
    return normalized


def _finite_timeout(
    value: object,
    name: str,
    maximum: int | float = MAX_POLICY_TIMEOUT_SECONDS,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimePolicyCompositionError(f"{name} must be finite and positive")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0 or normalized > maximum:
        raise RuntimePolicyCompositionError(f"{name} must be finite and positive")
    return normalized


def _approved_tool_timeout(value: object, name: str) -> float:
    maximum = _CONCEPTUAL_TOOL_TIMEOUTS.get(name)
    if maximum is None:
        raise RuntimePolicyCompositionError(f"{name} is not an approved executable tool")
    return _finite_timeout(value, f"timeout for {name}", maximum)


def _safe_name(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise RuntimePolicyCompositionError(f"{name} must be a safe identifier")
    return value


@dataclass(frozen=True, slots=True)
class EffectiveRuntimePolicy:
    """Immutable, application-owned limits used by one composed agent run."""

    max_agent_steps: int = MAX_AGENT_STEPS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_observations: int = MAX_OBSERVATIONS
    max_observation_size: int = MAX_OBSERVATION_SIZE
    total_observation_history_size: int = MAX_OBSERVATION_HISTORY_SIZE
    max_structured_proposal_repairs: int = 1
    max_identical_tool_call_repetitions: int = 2
    model_proposal_timeout_seconds: float = 60.0
    executable_tool_timeouts: tuple[tuple[str, float], ...] = ()
    policy_version: str = "phase5-runtime-v1"

    def __post_init__(self) -> None:
        _positive_int(self.max_agent_steps, "max_agent_steps", MAX_AGENT_STEPS)
        _positive_int(self.max_tool_calls, "max_tool_calls", MAX_TOOL_CALLS)
        _positive_int(self.max_observations, "max_observations", MAX_OBSERVATIONS)
        _bounded_observation_size(self.max_observation_size, "max_observation_size")
        total = _bounded_observation_size(
            self.total_observation_history_size, "total_observation_history_size"
        )
        if self.max_observation_size > total:
            raise RuntimePolicyCompositionError(
                "per-observation budget cannot exceed total observation history budget"
            )
        _positive_int(
            self.max_structured_proposal_repairs,
            "max_structured_proposal_repairs",
            1,
        )
        _positive_int(
            self.max_identical_tool_call_repetitions,
            "max_identical_tool_call_repetitions",
            2,
        )
        _finite_timeout(
            self.model_proposal_timeout_seconds,
            "model_proposal_timeout_seconds",
            MAX_MODEL_PROPOSAL_TIMEOUT_SECONDS,
        )
        if not isinstance(self.executable_tool_timeouts, tuple):
            raise RuntimePolicyCompositionError("executable_tool_timeouts must be a tuple")
        normalized: list[tuple[str, float]] = []
        names: set[str] = set()
        for item in self.executable_tool_timeouts:
            if not isinstance(item, tuple) or len(item) != 2:
                raise RuntimePolicyCompositionError("tool timeout entries are invalid")
            name = _safe_name(item[0], "tool timeout name")
            timeout = _approved_tool_timeout(item[1], name)
            if name in names:
                raise RuntimePolicyCompositionError("tool timeout names must be unique")
            names.add(name)
            normalized.append((name, timeout))
        if tuple(sorted(normalized)) != self.executable_tool_timeouts:
            raise RuntimePolicyCompositionError("tool timeouts must be canonically sorted")
        if not isinstance(self.policy_version, str) or not _SAFE_IDENTIFIER.fullmatch(
            self.policy_version
        ):
            raise RuntimePolicyCompositionError("policy_version must be a safe identifier")
        object.__setattr__(
            self, "model_proposal_timeout_seconds", float(self.model_proposal_timeout_seconds)
        )
        object.__setattr__(self, "executable_tool_timeouts", tuple(normalized))

    @classmethod
    def compose(cls, **kwargs: Any) -> "EffectiveRuntimePolicy":
        """Compatibility constructor for trusted application composition."""

        return compose_effective_runtime_policy(**kwargs)

    @property
    def fingerprint(self) -> str:
        """Stable digest of trusted configuration only."""

        return fingerprint_contract(self.to_dict())

    @property
    def tool_timeouts(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.executable_tool_timeouts))

    def timeout_for(self, name: ToolName | str) -> float | None:
        normalized = name.value if isinstance(name, ToolName) else name
        return self.tool_timeouts.get(normalized)

    def to_agent_limits(self) -> AgentLimits:
        """Project policy values into the legacy component contract."""

        timeout_values = dict(self.executable_tool_timeouts)
        return AgentLimits(
            max_agent_steps=self.max_agent_steps,
            max_structured_proposal_repairs=self.max_structured_proposal_repairs,
            max_identical_tool_call_repetitions=self.max_identical_tool_call_repetitions,
            model_proposal_timeout_seconds=self.model_proposal_timeout_seconds,
            rag_query_timeout_seconds=timeout_values.get("rag_query", 120.0),
            read_task_state_timeout_seconds=timeout_values.get("read_task_state", 5.0),
            write_task_state_timeout_seconds=timeout_values.get("write_task_state", 5.0),
            complete_task_timeout_seconds=timeout_values.get("complete_task", 5.0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "executable_tool_timeouts": tuple(
                {"name": name, "timeout_seconds": timeout}
                for name, timeout in self.executable_tool_timeouts
            ),
            "max_agent_steps": self.max_agent_steps,
            "max_identical_tool_call_repetitions": self.max_identical_tool_call_repetitions,
            "max_observation_size": self.max_observation_size,
            "max_observations": self.max_observations,
            "max_structured_proposal_repairs": self.max_structured_proposal_repairs,
            "max_tool_calls": self.max_tool_calls,
            "policy_version": self.policy_version,
            "model_proposal_timeout_seconds": self.model_proposal_timeout_seconds,
            "total_observation_history_size": self.total_observation_history_size,
        }


def _component_value(component: object | None, name: str) -> object | None:
    if component is None or not hasattr(component, name):
        return None
    try:
        return cast(object, getattr(component, name))
    except Exception as exc:
        raise RuntimePolicyCompositionError(f"could not inspect {name}") from exc


def _registry_timeouts(registry: object | None) -> tuple[tuple[str, float], ...]:
    if registry is None:
        return ()
    try:
        definitions = getattr(registry, "definitions")
    except Exception as exc:
        raise RuntimePolicyCompositionError("could not inspect tool definitions") from exc
    if not isinstance(definitions, tuple):
        raise RuntimePolicyCompositionError("tool definitions must be immutable")
    values: list[tuple[str, float]] = []
    for definition in definitions:
        try:
            handler = getattr(definition, "handler")
            name = getattr(getattr(definition, "name"), "value")
            timeout = getattr(definition, "timeout_seconds")
        except Exception as exc:
            raise RuntimePolicyCompositionError("tool definition metadata is invalid") from exc
        if handler is None:
            continue
        values.append((_safe_name(name, "tool name"), _approved_tool_timeout(timeout, name)))
    try:
        return tuple(sorted(values))
    except TypeError as exc:
        raise RuntimePolicyCompositionError("tool timeout metadata is invalid") from exc


def _assert_match(name: str, component_value: object | None, effective_value: object) -> None:
    if component_value is not None and component_value != effective_value:
        raise RuntimePolicyCompositionError(f"{name} does not match effective runtime policy")


def compose_effective_runtime_policy(
    *,
    effective_policy: EffectiveRuntimePolicy | None = None,
    proposal_adapter: object | None = None,
    registry: ToolRegistry | object | None = None,
    limits: AgentLimits | None = None,
    max_tool_calls: int | None = None,
    max_observations: int | None = None,
    max_observation_size: int | None = None,
    total_observation_history_size: int | None = None,
    max_structured_proposal_repairs: int | None = None,
    model_proposal_timeout_seconds: float | None = None,
) -> EffectiveRuntimePolicy:
    """Compose one policy and reject conflicting component settings.

    Missing settings are filled only by the approved constants.  Production
    components expose their settings for comparison; test-only structural
    fakes may omit them because they have no independent runtime policy.
    """

    if limits is not None and not isinstance(limits, AgentLimits):
        raise RuntimePolicyCompositionError("limits has an invalid type")
    if effective_policy is not None:
        if not isinstance(effective_policy, EffectiveRuntimePolicy):
            raise RuntimePolicyCompositionError("effective_policy has an invalid type")
        if max_tool_calls is not None:
            _positive_int(max_tool_calls, "max_tool_calls")
        if max_observations is not None:
            _positive_int(max_observations, "max_observations")
        if max_observation_size is not None:
            _bounded_observation_size(max_observation_size, "max_observation_size")
        if total_observation_history_size is not None:
            _bounded_observation_size(
                total_observation_history_size, "total_observation_history_size"
            )
        if max_structured_proposal_repairs is not None:
            _positive_int(max_structured_proposal_repairs, "max_structured_proposal_repairs")
        if model_proposal_timeout_seconds is not None:
            _finite_timeout(
                model_proposal_timeout_seconds,
                "model_proposal_timeout_seconds",
                MAX_MODEL_PROPOSAL_TIMEOUT_SECONDS,
            )
        _assert_match("max_tool_calls", max_tool_calls, effective_policy.max_tool_calls)
        _assert_match("max_observations", max_observations, effective_policy.max_observations)
        _assert_match(
            "max_observation_size", max_observation_size, effective_policy.max_observation_size
        )
        _assert_match(
            "total_observation_history_size",
            total_observation_history_size,
            effective_policy.total_observation_history_size,
        )
        _assert_match(
            "max_structured_proposal_repairs",
            max_structured_proposal_repairs,
            effective_policy.max_structured_proposal_repairs,
        )
        _assert_match(
            "model_proposal_timeout_seconds",
            model_proposal_timeout_seconds,
            effective_policy.model_proposal_timeout_seconds,
        )
        registry_timeouts = _registry_timeouts(registry)
        if limits is not None:
            _assert_limits_match(
                limits,
                effective_policy,
                allow_canonical_tool_timeout_overrides=registry is not None,
            )
            _assert_non_executable_limits_are_canonical(limits, registry_timeouts)
        _assert_adapter_match(proposal_adapter, effective_policy)
        if registry is not None and registry_timeouts != effective_policy.executable_tool_timeouts:
            raise RuntimePolicyCompositionError("tool timeouts do not match effective policy")
        return effective_policy

    base_limits = limits if limits is not None else AgentLimits()
    effective_max_repairs = (
        max_structured_proposal_repairs
        if max_structured_proposal_repairs is not None
        else base_limits.max_structured_proposal_repairs
    )
    effective_model_timeout = (
        model_proposal_timeout_seconds
        if model_proposal_timeout_seconds is not None
        else base_limits.model_proposal_timeout_seconds
    )
    if limits is not None:
        _assert_match(
            "max_structured_proposal_repairs",
            max_structured_proposal_repairs,
            base_limits.max_structured_proposal_repairs,
        )
        _assert_match(
            "model_proposal_timeout_seconds",
            model_proposal_timeout_seconds,
            base_limits.model_proposal_timeout_seconds,
        )
    registry_timeouts = _registry_timeouts(registry)
    policy = EffectiveRuntimePolicy(
        max_agent_steps=base_limits.max_agent_steps,
        max_tool_calls=MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls,
        max_observations=MAX_OBSERVATIONS if max_observations is None else max_observations,
        max_observation_size=(
            MAX_OBSERVATION_SIZE if max_observation_size is None else max_observation_size
        ),
        total_observation_history_size=(
            MAX_OBSERVATION_HISTORY_SIZE
            if total_observation_history_size is None
            else total_observation_history_size
        ),
        max_structured_proposal_repairs=effective_max_repairs,
        max_identical_tool_call_repetitions=base_limits.max_identical_tool_call_repetitions,
        model_proposal_timeout_seconds=effective_model_timeout,
        executable_tool_timeouts=registry_timeouts,
    )
    if limits is not None:
        _assert_limits_match(limits, policy, allow_canonical_tool_timeout_overrides=True)
        _assert_non_executable_limits_are_canonical(limits, registry_timeouts)
    _assert_adapter_match(proposal_adapter, policy)
    return policy


def _assert_limits_match(
    limits: AgentLimits,
    policy: EffectiveRuntimePolicy,
    *,
    allow_canonical_tool_timeout_overrides: bool = False,
) -> None:
    _assert_match("max_agent_steps", limits.max_agent_steps, policy.max_agent_steps)
    _assert_match(
        "max_structured_proposal_repairs",
        limits.max_structured_proposal_repairs,
        policy.max_structured_proposal_repairs,
    )
    _assert_match(
        "max_identical_tool_call_repetitions",
        limits.max_identical_tool_call_repetitions,
        policy.max_identical_tool_call_repetitions,
    )
    _assert_match(
        "model_proposal_timeout_seconds",
        limits.model_proposal_timeout_seconds,
        policy.model_proposal_timeout_seconds,
    )
    policy_timeouts = dict(policy.executable_tool_timeouts)
    component_timeouts = {
        "academic.get_assignment": 5.0,
        "academic.get_course": 5.0,
        "academic.list_assignments": 5.0,
        "academic.list_courses": 5.0,
        "academic.list_documents": 5.0,
        "study_task.list": 5.0,
        "study_task.get": 5.0,
        "study_task.create": 5.0,
        "study_task.update": 5.0,
        "study_task.complete": 5.0,
        "rag.answer": 120.0,
        "rag_query": limits.rag_query_timeout_seconds,
        "read_task_state": limits.read_task_state_timeout_seconds,
        "write_task_state": limits.write_task_state_timeout_seconds,
        "complete_task": limits.complete_task_timeout_seconds,
    }
    for name, timeout in policy_timeouts.items():
        component_timeout = component_timeouts.get(name)
        if (
            allow_canonical_tool_timeout_overrides
            and component_timeout == _CONCEPTUAL_TOOL_TIMEOUTS.get(name)
        ):
            continue
        _assert_match(f"timeout for {name}", component_timeout, timeout)


def _assert_non_executable_limits_are_canonical(
    limits: AgentLimits | None,
    registry_timeouts: tuple[tuple[str, float], ...],
) -> None:
    """Reject legacy timeout settings silently dropped for disabled tools."""

    if limits is None:
        return
    component_timeouts = {
        "academic.get_assignment": 5.0,
        "academic.get_course": 5.0,
        "academic.list_assignments": 5.0,
        "academic.list_courses": 5.0,
        "academic.list_documents": 5.0,
        "study_task.list": 5.0,
        "study_task.get": 5.0,
        "study_task.create": 5.0,
        "study_task.update": 5.0,
        "study_task.complete": 5.0,
        "rag.answer": 120.0,
        "rag_query": limits.rag_query_timeout_seconds,
        "read_task_state": limits.read_task_state_timeout_seconds,
        "write_task_state": limits.write_task_state_timeout_seconds,
        "complete_task": limits.complete_task_timeout_seconds,
    }
    executable_names = {name for name, _ in registry_timeouts}
    for name, canonical_timeout in _CONCEPTUAL_TOOL_TIMEOUTS.items():
        if name not in executable_names:
            _assert_match(
                f"disabled timeout for {name}",
                component_timeouts[name],
                canonical_timeout,
            )


def _assert_adapter_match(proposal_adapter: object | None, policy: EffectiveRuntimePolicy) -> None:
    repair_value = _component_value(proposal_adapter, "max_structured_proposal_repairs")
    if repair_value is None:
        repair_value = _component_value(proposal_adapter, "max_repair_attempts")
    timeout_value = _component_value(proposal_adapter, "model_proposal_timeout_seconds")
    if timeout_value is None:
        timeout_value = _component_value(proposal_adapter, "request_timeout_seconds")
    _assert_match(
        "proposal repair limit",
        repair_value,
        policy.max_structured_proposal_repairs,
    )
    _assert_match(
        "model proposal timeout",
        timeout_value,
        policy.model_proposal_timeout_seconds,
    )


__all__ = [
    "MAX_AGENT_STEPS",
    "MAX_OBSERVATION_HISTORY_SIZE",
    "MAX_OBSERVATION_SIZE",
    "MAX_OBSERVATIONS",
    "MAX_TOOL_CALLS",
    "USEFUL_RAG_CALLS_PER_REQUEST_MAX",
    "EffectiveRuntimePolicy",
    "RuntimePolicyCompositionError",
    "compose_effective_runtime_policy",
]
