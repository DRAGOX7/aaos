"""Trusted, capability-based permission policy for Phase 5 tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from aaos.rag import CourseScope, GlobalScope, HistoricalScope, RagScope

from .contracts import (
    AgentErrorCode,
    PermissionClass,
    PermissionDecision,
    TrustedTaskScope,
)
from .runtime_policy import (
    EffectiveRuntimePolicy,
    RuntimePolicyCompositionError,
    compose_effective_runtime_policy,
)


@dataclass(frozen=True, slots=True)
class ScopeAuthorizationDecision:
    """Trusted result of comparing a proposed RAG scope with a task grant."""

    allowed: bool
    reason_code: AgentErrorCode

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a boolean")
        if self.reason_code not in {AgentErrorCode.SCOPE_GRANTED, AgentErrorCode.SCOPE_DENIED}:
            raise ValueError("reason_code must be a scope authorization code")


def _positive_scope_id(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def parse_proposed_scope(value: object) -> RagScope:
    """Parse only the typed scope shape accepted by the Phase 4 boundary."""

    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError("scope must be an object")
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise ValueError("scope.kind must be a string")
    normalized_kind = kind.strip().casefold()
    if normalized_kind == "global":
        if set(value) != {"kind"}:
            raise ValueError("global scope contains unknown fields")
        return GlobalScope()
    if normalized_kind == "course":
        if set(value) != {"kind", "course_id"}:
            raise ValueError("course scope contains unknown fields")
        return CourseScope(_positive_scope_id(value.get("course_id"), "course_id"))
    if normalized_kind == "historical":
        if set(value) != {"kind", "document_version_ids"}:
            raise ValueError("historical scope contains unknown fields")
        ids = value.get("document_version_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("document_version_ids must be a non-empty list")
        return HistoricalScope(
            tuple(_positive_scope_id(item, "document_version_ids") for item in ids)
        )
    raise ValueError("scope.kind is unsupported")


def authorize_scope(
    proposed_scope: RagScope, trusted_task_scope: TrustedTaskScope | None
) -> ScopeAuthorizationDecision:
    """Authorize a model-proposed scope against an application-owned grant.

    The relation is intentionally fail-closed: course and global grants are
    exact-identity grants, while historical grants allow only an explicit
    subset of their version allowlist. No natural-language request or model
    observation participates in this decision.
    """

    denied = ScopeAuthorizationDecision(False, AgentErrorCode.SCOPE_DENIED)
    if not isinstance(proposed_scope, (CourseScope, GlobalScope, HistoricalScope)):
        return denied
    if not isinstance(trusted_task_scope, TrustedTaskScope):
        return denied
    trusted = trusted_task_scope.scope
    if isinstance(trusted, CourseScope) and isinstance(proposed_scope, CourseScope):
        allowed = trusted.course_id == proposed_scope.course_id
    elif isinstance(trusted, GlobalScope) and isinstance(proposed_scope, GlobalScope):
        allowed = True
    elif isinstance(trusted, HistoricalScope) and isinstance(proposed_scope, HistoricalScope):
        proposed_ids = proposed_scope.document_version_ids
        allowed = len(set(proposed_ids)) == len(proposed_ids) and set(proposed_ids).issubset(
            trusted.document_version_ids
        )
    else:
        allowed = False
    return ScopeAuthorizationDecision(
        allowed,
        AgentErrorCode.SCOPE_GRANTED if allowed else AgentErrorCode.SCOPE_DENIED,
    )


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """A request-local explicit capability set.

    An empty set is intentional: absent permission state denies every tool.
    Model text, retrieved data, and observations never enter this policy.
    """

    granted_permissions: frozenset[PermissionClass] = frozenset()
    policy_version: str = "phase5-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.granted_permissions, frozenset):
            raise TypeError("granted_permissions must be a frozenset")
        if any(
            not isinstance(permission, PermissionClass) for permission in self.granted_permissions
        ):
            raise ValueError("permission contains an unknown capability")
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise ValueError("policy_version must be a non-blank string")
        if not self.policy_version[0].isalnum() or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in self.policy_version
        ):
            raise ValueError("policy_version must be a safe identifier")

    @classmethod
    def from_values(cls, permissions: Iterable[PermissionClass]) -> PermissionPolicy:
        if isinstance(permissions, (str, bytes)):
            raise ValueError("permission contains an unknown capability")
        values = frozenset(permissions)
        return cls(granted_permissions=values)

    def evaluate(self, definition: object) -> PermissionDecision:
        from .registry import ToolDefinition

        if not isinstance(definition, ToolDefinition):
            return PermissionDecision(
                allowed=False,
                required_permission=PermissionClass.READ_ONLY,
                reason_code=AgentErrorCode.UNKNOWN_PERMISSION,
                policy_version=self.policy_version,
            )
        required = getattr(definition, "required_permission", None)
        if not isinstance(required, PermissionClass):
            return PermissionDecision(
                allowed=False,
                required_permission=PermissionClass.READ_ONLY,
                reason_code=AgentErrorCode.UNKNOWN_PERMISSION,
                policy_version=self.policy_version,
            )
        allowed = required in self.granted_permissions
        return PermissionDecision(
            allowed=allowed,
            required_permission=required,
            reason_code=(
                AgentErrorCode.PERMISSION_GRANTED if allowed else AgentErrorCode.PERMISSION_DENIED
            ),
            policy_version=self.policy_version,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "granted_permissions": tuple(
                sorted(permission.value for permission in self.granted_permissions)
            ),
            "policy_version": self.policy_version,
        }


__all__ = [
    "EffectiveRuntimePolicy",
    "PermissionPolicy",
    "RuntimePolicyCompositionError",
    "ScopeAuthorizationDecision",
    "authorize_scope",
    "compose_effective_runtime_policy",
    "parse_proposed_scope",
]
