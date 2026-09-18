"""Deterministic worker double used by Phase 8 contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)
from .worker_protocol import WorkerProtocolError


class FakeWorkerScenario(StrEnum):
    SUCCESS = "SUCCESS"
    REJECT_BEFORE_EXECUTION = "REJECT_BEFORE_EXECUTION"
    TIMEOUT_AFTER_START = "TIMEOUT_AFTER_START"
    CRASH_AFTER_START = "CRASH_AFTER_START"
    OVERSIZED_OUTPUT = "OVERSIZED_OUTPUT"


@dataclass(frozen=True, slots=True)
class FakeWorker:
    scenario: FakeWorkerScenario = FakeWorkerScenario.SUCCESS

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, FakeWorkerScenario):
            raise TypeError("scenario must be a FakeWorkerScenario")

    def execute(self, request: ComputationRequest) -> ComputationResult:
        if not isinstance(request, ComputationRequest):
            raise TypeError("request must be a ComputationRequest")
        if self.scenario is FakeWorkerScenario.OVERSIZED_OUTPUT:
            raise WorkerProtocolError("OUTPUT_TOO_LARGE")
        if self.scenario is FakeWorkerScenario.SUCCESS:
            stdout = "2\n"
            return ComputationResult(
                request_id=request.request_id,
                status=ComputationStatus.SUCCEEDED,
                stdout=stdout,
                stderr="",
                result_json='{"value":2}',
                output_sha256=sha256(stdout.encode("utf-8")).hexdigest(),
                duration_ms=1,
                worker_id="fake-worker",
                contract_version=COMPUTATION_CONTRACT_VERSION,
            )
        if self.scenario is FakeWorkerScenario.REJECT_BEFORE_EXECUTION:
            return ComputationResult(
                request_id=request.request_id,
                status=ComputationStatus.FAILED,
                stdout="",
                stderr="request rejected",
                result_json=None,
                output_sha256=None,
                duration_ms=0,
                worker_id="fake-worker",
                contract_version=COMPUTATION_CONTRACT_VERSION,
            )
        return ComputationResult(
            request_id=request.request_id,
            status=ComputationStatus.UNKNOWN_OUTCOME,
            stdout="",
            stderr="worker outcome unknown",
            result_json=None,
            output_sha256=None,
            duration_ms=1,
            worker_id="fake-worker",
            contract_version=COMPUTATION_CONTRACT_VERSION,
        )


__all__ = ["FakeWorker", "FakeWorkerScenario"]
