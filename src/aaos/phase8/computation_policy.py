"""Trusted capability policy for the internal Phase 8 computation service."""

from __future__ import annotations

from dataclasses import dataclass

from aaos.agent.contracts import TrustedTaskScope
from aaos.rag import CourseScope, GlobalScope, HistoricalScope

from .computation_contracts import ComputationRequest

_SCOPE_KINDS = frozenset({"course", "global", "historical"})


@dataclass(frozen=True, slots=True)
class ComputationCapabilityPolicy:
    """Application-owned capability decision; no model field participates."""

    enabled: bool = False
    allowed_scope_kinds: frozenset[str] = frozenset()
    policy_version: str = "phase8-computation-policy-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        if not isinstance(self.allowed_scope_kinds, frozenset):
            raise TypeError("allowed_scope_kinds must be a frozenset")
        if not self.allowed_scope_kinds.issubset(_SCOPE_KINDS):
            raise ValueError("allowed_scope_kinds contains an unsupported scope")
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("policy_version must be non-blank")

    def is_allowed(self, task_scope: TrustedTaskScope) -> bool:
        if not self.enabled or not isinstance(task_scope, TrustedTaskScope):
            return False
        if isinstance(task_scope.scope, CourseScope):
            kind = "course"
        elif isinstance(task_scope.scope, GlobalScope):
            kind = "global"
        elif isinstance(task_scope.scope, HistoricalScope):
            kind = "historical"
        else:
            return False
        return kind in self.allowed_scope_kinds


def validate_computation_request(
    request: ComputationRequest, *, policy: ComputationCapabilityPolicy
) -> None:
    """Validate a trusted request without performing any I/O."""

    if not isinstance(request, ComputationRequest):
        raise TypeError("request must be a ComputationRequest")
    if not isinstance(policy, ComputationCapabilityPolicy):
        raise TypeError("policy must be a ComputationCapabilityPolicy")
    if not policy.enabled or not policy.allowed_scope_kinds:
        raise ValueError("computation capability denied")


__all__ = ["ComputationCapabilityPolicy", "validate_computation_request"]
