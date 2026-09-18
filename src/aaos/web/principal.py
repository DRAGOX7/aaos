"""Trusted local principal resolution for Phase 7E mutations."""

from __future__ import annotations

from typing import Protocol

from aaos.approval.contracts import ActorIdentity, TrustedApproverContext
from aaos.config import DEFAULT_SETTINGS, AppSettings


class PrincipalUnavailable(RuntimeError):
    """The trusted local principal is not configured."""


class LocalApproverContextProvider(Protocol):
    def current(self) -> TrustedApproverContext:
        """Return the server-derived trusted approver context."""


class LocalPrincipalProvider:
    """Resolve one local operator from immutable, non-secret settings."""

    def __init__(
        self,
        settings: AppSettings = DEFAULT_SETTINGS,
        *,
        trusted_value: TrustedApproverContext | None = None,
    ) -> None:
        if not isinstance(settings, AppSettings):
            raise TypeError("settings must be an AppSettings")
        if trusted_value is not None and not isinstance(trusted_value, TrustedApproverContext):
            raise TypeError("trusted_value must be a TrustedApproverContext")
        self._settings = settings
        self._trusted_value = trusted_value

    @classmethod
    def from_trusted_value(cls, value: TrustedApproverContext) -> "LocalPrincipalProvider":
        if not isinstance(value, TrustedApproverContext):
            raise TypeError("value must be a TrustedApproverContext")
        return cls(trusted_value=value)

    def current(self) -> TrustedApproverContext:
        if self._trusted_value is not None:
            return self._trusted_value
        if self._settings.local_approver_identity is None:
            raise PrincipalUnavailable("local approver context is unavailable")
        context_id = self._settings.local_approver_context_id
        if context_id is None:
            raise PrincipalUnavailable("local approver context is unavailable")
        return TrustedApproverContext(self._settings.local_approver_identity, context_id)

    def actor(self) -> ActorIdentity:
        return self.current().approver_identity


__all__ = ["LocalApproverContextProvider", "LocalPrincipalProvider", "PrincipalUnavailable"]
