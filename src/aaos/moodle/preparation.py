"""Offline preparation gate for a human-approved Moodle capability record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from aaos.moodle.models import MoodleCapability, MoodleSettings, validate_capability

CapabilityStatus = Literal["ready", "blocked"]
_SECRET_FIELD_TERMS = ("password", "token", "cookie", "session", "authorization")


@dataclass(frozen=True, slots=True)
class CapabilityPreparationResult:
    status: CapabilityStatus
    reason_code: str
    capability: MoodleCapability | None


def _blocked(reason_code: str) -> CapabilityPreparationResult:
    return CapabilityPreparationResult("blocked", reason_code, None)


def prepare_capability(
    settings: MoodleSettings, observed: Mapping[str, object]
) -> CapabilityPreparationResult:
    """Convert synthetic/offline evidence into a validated capability, never performing I/O."""
    if any(
        isinstance(key, str) and any(term in key.lower() for term in _SECRET_FIELD_TERMS)
        for key in observed
    ):
        return _blocked("secret_field_present")

    availability_key = (
        "api_capability_available" if settings.mode == "api" else "browser_capability_available"
    )
    if observed.get(availability_key) is not True:
        return _blocked(
            "api_capability_unavailable"
            if settings.mode == "api"
            else "browser_capability_unavailable"
        )

    complete_scope = observed.get("complete_scope")
    if (
        settings.source_system.endswith(":student-all-courses")
        and complete_scope != "all-accessible-courses"
    ):
        return _blocked("scope_not_authoritative")

    entity_scope = observed.get("entity_scope")
    if not isinstance(entity_scope, tuple) or set(entity_scope) != {
        "course",
        "assignment",
        "announcement",
        "remote_file_metadata",
    }:
        return _blocked("entity_scope_incomplete")

    required_keys = {
        "complete_scope",
        "entity_scope",
        "approved_api_operations",
        "approved_browser_routes",
        "pagination_strategy",
        "identity_strategy",
        "timestamp_strategy",
        "redirect_policy",
        "redirect_origins",
        "required_version",
        "evidence_notes",
    }
    if not required_keys.issubset(observed):
        return _blocked("capability_incomplete")

    try:
        capability = MoodleCapability(
            source_system=settings.source_system,
            base_origin=settings.base_origin,
            mode=settings.mode,
            complete_scope=observed["complete_scope"],  # type: ignore[arg-type]
            entity_scope=observed["entity_scope"],  # type: ignore[arg-type]
            approved_api_operations=observed["approved_api_operations"],  # type: ignore[arg-type]
            approved_browser_routes=observed["approved_browser_routes"],  # type: ignore[arg-type]
            pagination_strategy=observed["pagination_strategy"],  # type: ignore[arg-type]
            identity_strategy=observed["identity_strategy"],  # type: ignore[arg-type]
            timestamp_strategy=observed["timestamp_strategy"],  # type: ignore[arg-type]
            redirect_policy=observed["redirect_policy"],  # type: ignore[arg-type]
            redirect_origins=observed["redirect_origins"],  # type: ignore[arg-type]
            required_version=observed["required_version"],  # type: ignore[arg-type]
            evidence_notes=observed["evidence_notes"],  # type: ignore[arg-type]
        )
        validate_capability(settings, capability)
    except (TypeError, ValueError):
        return _blocked("capability_invalid")

    return CapabilityPreparationResult("ready", "ready", capability)
