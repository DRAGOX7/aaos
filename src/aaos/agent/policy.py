"""Trusted, capability-based permission policy for Phase 5 tools."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .contracts import AgentErrorCode, PermissionClass, PermissionDecision


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


__all__ = ["PermissionPolicy"]
