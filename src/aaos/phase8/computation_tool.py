"""Trusted Phase 8 computation adapter and model-safe projection."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from aaos.agent.contracts import ToolObservation

from .computation_contracts import ComputationRequest, ComputationResult, ComputationStatus


class ComputationPermission(StrEnum):
    """Closed trusted capability decision; never model-selectable."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class ComputationToolAdapter(Protocol):
    def execute(self, request: ComputationRequest) -> ComputationResult: ...


def project_computation_observation(result: ComputationResult) -> ToolObservation:
    """Project one bounded result without forwarding worker text or authority."""

    if not isinstance(result, ComputationResult):
        raise TypeError("result must be a ComputationResult")
    if result.status is ComputationStatus.SUCCEEDED:
        return ToolObservation(
            kind="phase8_computation",
            status="OK",
            text="computation completed",
            fields=(("duration_ms", result.duration_ms), ("status", result.status.value)),
        )
    messages = {
        ComputationStatus.FAILED: "computation failed",
        ComputationStatus.TIMED_OUT: "computation timed out",
        ComputationStatus.CANCELLED: "computation cancelled",
        ComputationStatus.UNKNOWN_OUTCOME: "computation outcome is unknown",
    }
    return ToolObservation(
        kind="phase8_computation",
        status="ERROR",
        text=messages[result.status],
        fields=(("duration_ms", result.duration_ms), ("status", result.status.value)),
    )


__all__ = ["ComputationPermission", "ComputationToolAdapter", "project_computation_observation"]
