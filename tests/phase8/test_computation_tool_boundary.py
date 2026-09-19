from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import pytest

from aaos.agent.contracts import ToolName, ToolObservation
from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)
from aaos.phase8.computation_policy import ComputationCapabilityPolicy


def _request() -> ComputationRequest:
    content = b"safe input"
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(ComputationInput("input-1", "text/plain", sha256(content).hexdigest(), content),),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def _result(status: ComputationStatus = ComputationStatus.SUCCEEDED) -> ComputationResult:
    return ComputationResult(
        request_id="request-1",
        status=status,
        stdout="2\n" if status is ComputationStatus.SUCCEEDED else "",
        stderr="" if status is ComputationStatus.SUCCEEDED else "worker outcome unknown",
        result_json='{"value":2}' if status is ComputationStatus.SUCCEEDED else None,
        output_sha256=None,
        duration_ms=1,
        worker_id="fake-worker",
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


@dataclass
class RecordingAdapter:
    result: ComputationResult
    calls: int = 0

    def execute(self, request: ComputationRequest) -> ComputationResult:
        self.calls += 1
        assert request is not None
        return self.result


def _allowed_policy() -> ComputationCapabilityPolicy:
    return ComputationCapabilityPolicy(enabled=True, allowed_scope_kinds=frozenset({"global"}))


def test_trusted_service_executes_once_without_model_visible_registry_tool() -> None:
    from aaos.phase8.computation_service import TrustedComputationService

    adapter = RecordingAdapter(_result())
    service = TrustedComputationService(adapter)

    result = service.run(_request(), policy=_allowed_policy())

    assert result.status is ComputationStatus.SUCCEEDED
    assert adapter.calls == 1
    assert "computation" not in {tool.value for tool in ToolName}


def test_policy_denial_fails_before_adapter_and_is_bounded() -> None:
    from aaos.phase8.computation_service import TrustedComputationService

    adapter = RecordingAdapter(_result())
    service = TrustedComputationService(adapter)

    result = service.run(_request(), policy=ComputationCapabilityPolicy())

    assert result.status is ComputationStatus.FAILED
    assert result.stderr == "computation capability denied"
    assert adapter.calls == 0


def test_unknown_outcome_is_preserved_without_automatic_retry() -> None:
    from aaos.phase8.computation_service import TrustedComputationService

    adapter = RecordingAdapter(_result(ComputationStatus.UNKNOWN_OUTCOME))
    service = TrustedComputationService(adapter)

    result = service.run(_request(), policy=_allowed_policy())

    assert result.status is ComputationStatus.UNKNOWN_OUTCOME
    assert adapter.calls == 1


def test_projection_is_closed_and_does_not_expose_raw_worker_details() -> None:
    from aaos.phase8.computation_tool import project_computation_observation

    observation = project_computation_observation(_result())

    assert isinstance(observation, ToolObservation)
    assert observation.kind == "phase8_computation"
    assert dict(observation.fields) == {"duration_ms": 1, "status": "SUCCEEDED"}
    assert observation.text == "computation completed"


def test_projection_maps_unknown_outcome_to_safe_terminal_observation() -> None:
    from aaos.phase8.computation_tool import project_computation_observation

    observation = project_computation_observation(_result(ComputationStatus.UNKNOWN_OUTCOME))

    assert observation.kind == "phase8_computation"
    assert observation.status == "ERROR"
    assert observation.text == "computation outcome is unknown"
    assert dict(observation.fields)["status"] == "UNKNOWN_OUTCOME"


def test_invalid_request_is_rejected_before_execution() -> None:
    from aaos.phase8.computation_service import TrustedComputationService

    adapter = RecordingAdapter(_result())
    service = TrustedComputationService(adapter)
    with pytest.raises(TypeError):
        service.run(object(), policy=_allowed_policy())  # type: ignore[arg-type]
    assert adapter.calls == 0
