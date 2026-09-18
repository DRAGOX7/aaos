"""Fail-closed same-origin request checks for Phase 7E mutations."""

from __future__ import annotations

from collections.abc import Iterable

_DEFAULT_HOSTS = frozenset({"127.0.0.1:8000", "localhost:8000", "[::1]:8000"})
_DEFAULT_ORIGINS = frozenset(
    {"http://127.0.0.1:3000", "http://localhost:3000", "http://[::1]:3000"}
)
_AUTHORITY_FIELDS = frozenset(
    {"actor_identity", "approver_identity", "approver_context_id", "role", "capability"}
)


class MutationRequestRejected(ValueError):
    """A browser mutation request failed the local same-origin fence."""


def enforce_mutation_fence(
    *,
    host: str | None,
    origin: str | None,
    content_type: str | None,
    authority_fields: Iterable[str] = (),
    allowed_hosts: Iterable[str] = _DEFAULT_HOSTS,
    allowed_origins: Iterable[str] = _DEFAULT_ORIGINS,
) -> None:
    """Require an exact loopback host/origin and closed JSON content type."""

    if not isinstance(host, str) or host not in frozenset(allowed_hosts):
        raise MutationRequestRejected("mutation host is not allowlisted")
    if not isinstance(origin, str) or origin not in frozenset(allowed_origins):
        raise MutationRequestRejected("mutation origin is not allowlisted")
    if content_type != "application/json":
        raise MutationRequestRejected("mutation content type is not allowed")
    fields = tuple(authority_fields)
    if any(field in _AUTHORITY_FIELDS for field in fields):
        raise MutationRequestRejected("browser authority fields are not accepted")


__all__ = ["MutationRequestRejected", "enforce_mutation_fence"]
