"""Trusted internal computation orchestration for Phase 8."""

from __future__ import annotations

from aaos.agent.contracts import ToolObservation

from .computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)
from .computation_policy import ComputationCapabilityPolicy, validate_computation_request
from .computation_tool import ComputationToolAdapter, project_computation_observation


def _service_result(
    request: ComputationRequest,
    status: ComputationStatus,
    message: str,
) -> ComputationResult:
    return ComputationResult(
        request_id=request.request_id,
        status=status,
        stdout="",
        stderr=message,
        result_json=None,
        output_sha256=None,
        duration_ms=0,
        worker_id="phase8-service",
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


class TrustedComputationService:
    """Apply trusted capability policy and invoke exactly one adapter attempt."""

    def __init__(self, adapter: ComputationToolAdapter) -> None:
        if not hasattr(adapter, "execute") or not callable(adapter.execute):
            raise TypeError("adapter must provide execute")
        self._adapter = adapter

    def run(
        self,
        request: ComputationRequest,
        *,
        policy: ComputationCapabilityPolicy,
    ) -> ComputationResult:
        if not isinstance(request, ComputationRequest):
            raise TypeError("request must be a ComputationRequest")
        try:
            validate_computation_request(request, policy=policy)
        except (TypeError, ValueError):
            return _service_result(
                request,
                ComputationStatus.FAILED,
                "computation capability denied",
            )
        try:
            result = self._adapter.execute(request)
        except Exception:
            return _service_result(
                request,
                ComputationStatus.UNKNOWN_OUTCOME,
                "computation outcome is unknown",
            )
        if not isinstance(result, ComputationResult) or result.request_id != request.request_id:
            return _service_result(
                request,
                ComputationStatus.UNKNOWN_OUTCOME,
                "computation outcome is unknown",
            )
        return result

    def run_observation(
        self,
        request: ComputationRequest,
        *,
        policy: ComputationCapabilityPolicy,
    ) -> ToolObservation:
        return project_computation_observation(self.run(request, policy=policy))


__all__ = ["TrustedComputationService"]
