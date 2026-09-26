from __future__ import annotations

from hashlib import sha256

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationStatus,
)
from aaos.phase8.fake_worker import FakeWorker, FakeWorkerScenario
from aaos.phase8.worker_protocol import WorkerProtocolError


def _request() -> ComputationRequest:
    content = b"input"
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(ComputationInput("input-1", "text/plain", sha256(content).hexdigest(), content),),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        (FakeWorkerScenario.SUCCESS, ComputationStatus.SUCCEEDED),
        (FakeWorkerScenario.REJECT_BEFORE_EXECUTION, ComputationStatus.FAILED),
        (FakeWorkerScenario.TIMEOUT_AFTER_START, ComputationStatus.UNKNOWN_OUTCOME),
        (FakeWorkerScenario.CRASH_AFTER_START, ComputationStatus.UNKNOWN_OUTCOME),
    ],
)
def test_fake_worker_has_deterministic_closed_outcomes(
    scenario: FakeWorkerScenario, status: ComputationStatus
) -> None:
    worker = FakeWorker(scenario=scenario)
    result = worker.execute(_request())
    assert result.status is status
    assert result.request_id == "request-1"
    assert result.worker_id == "fake-worker"
    assert "traceback" not in result.stderr.lower()
    assert worker.execute(_request()) == result


def test_oversized_output_is_rejected_before_a_result_can_escape() -> None:
    worker = FakeWorker(scenario=FakeWorkerScenario.OVERSIZED_OUTPUT)
    with pytest.raises(WorkerProtocolError, match="output"):
        worker.execute(_request())
