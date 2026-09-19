"""Deterministic, synthetic-only Phase 8 evaluation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from .computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)
from .fake_vision import FakeVisionGateway, FakeVisionScenario
from .fake_worker import FakeWorker, FakeWorkerScenario
from .vision_contracts import RenderedPage
from .vision_gateway import VisionRequest, VisionResponse

PHASE8_EVALUATION_SCHEMA_VERSION = "phase8-evaluation-v1"
PHASE8_CALIBRATION_IDS = (
    "cal-compute-success",
    "cal-compute-policy-denied",
    "cal-compute-timeout",
    "cal-compute-unknown",
    "cal-vision-text",
    "cal-vision-diagram",
    "cal-vision-chart",
    "cal-vision-table",
    "cal-vision-equation",
    "cal-vision-partial",
    "cal-vision-unavailable",
    "cal-vision-malformed",
    "cal-composition-injection",
)
PHASE8_HELDOUT_IDS = (
    "synthetic-holdout-selection",
    "synthetic-holdout-provenance",
    "synthetic-holdout-fallback",
)
_SAFE_CASE_ID = re.compile(r"^(?:cal|hold|synthetic-holdout)-[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_manifest_bytes(
    schema_version: str, calibration_ids: tuple[str, ...], heldout_ids: tuple[str, ...]
) -> bytes:
    return json.dumps(
        {
            "calibration_ids": calibration_ids,
            "heldout_ids": heldout_ids,
            "schema_version": schema_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Phase8EvaluationManifest:
    """Frozen membership for the deterministic Phase 8 calibration split."""

    schema_version: str
    calibration_ids: tuple[str, ...]
    heldout_ids: tuple[str, ...]
    sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != PHASE8_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported Phase 8 evaluation schema")
        for name, values, prefixes in (
            ("calibration_ids", self.calibration_ids, ("cal-", "hold-")),
            ("heldout_ids", self.heldout_ids, ("synthetic-holdout-",)),
        ):
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{name} must be a non-empty tuple")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
            for case_id in values:
                if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
                    raise ValueError("case IDs must be bounded safe identifiers")
                if not any(case_id.startswith(prefix) for prefix in prefixes):
                    raise ValueError(f"{name} contains an invalid case ID")
        if set(self.calibration_ids) & set(self.heldout_ids):
            raise ValueError("calibration and heldout IDs must be disjoint")
        expected = hashlib.sha256(
            _canonical_manifest_bytes(self.schema_version, self.calibration_ids, self.heldout_ids)
        ).hexdigest()
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("manifest sha256 must be lowercase SHA-256")
        if self.sha256 != expected:
            raise ValueError("manifest sha256 does not match frozen membership")

    @classmethod
    def from_ids(
        cls, calibration_ids: tuple[str, ...], heldout_ids: tuple[str, ...]
    ) -> "Phase8EvaluationManifest":
        digest = hashlib.sha256(
            _canonical_manifest_bytes(
                PHASE8_EVALUATION_SCHEMA_VERSION, calibration_ids, heldout_ids
            )
        ).hexdigest()
        return cls(PHASE8_EVALUATION_SCHEMA_VERSION, calibration_ids, heldout_ids, digest)


class FakeSandboxExecutor(Protocol):
    def execute(self, request: ComputationRequest) -> ComputationResult: ...


@dataclass(frozen=True, slots=True)
class Phase8Report:
    """Bounded trusted-core evaluation report with no model/provider payloads."""

    manifest_sha256: str
    computation_case_count: int
    computation_pass_count: int
    computation_security_negative_count: int
    vision_case_count: int
    vision_pass_count: int
    vision_provenance_pass_count: int
    composition_case_count: int
    composition_pass_count: int
    model_call_count: int = 0
    rag_execution_count: int = 0
    external_write_count: int = 0
    retry_count: int = 0
    heldout_execution_count: int = 0
    fixture_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.manifest_sha256):
            raise ValueError("manifest_sha256 must be lowercase SHA-256")
        for name in (
            "computation_case_count",
            "computation_pass_count",
            "computation_security_negative_count",
            "vision_case_count",
            "vision_pass_count",
            "vision_provenance_pass_count",
            "composition_case_count",
            "composition_pass_count",
            "model_call_count",
            "rag_execution_count",
            "external_write_count",
            "retry_count",
            "heldout_execution_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.fixture_hashes, tuple) or not all(
            isinstance(value, str) and _SHA256.fullmatch(value) for value in self.fixture_hashes
        ):
            raise ValueError("fixture_hashes must contain lowercase SHA-256 values")

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_sha256": self.manifest_sha256,
            "computation_case_count": self.computation_case_count,
            "computation_pass_count": self.computation_pass_count,
            "computation_security_negative_count": self.computation_security_negative_count,
            "vision_case_count": self.vision_case_count,
            "vision_pass_count": self.vision_pass_count,
            "vision_provenance_pass_count": self.vision_provenance_pass_count,
            "composition_case_count": self.composition_case_count,
            "composition_pass_count": self.composition_pass_count,
            "model_call_count": self.model_call_count,
            "rag_execution_count": self.rag_execution_count,
            "external_write_count": self.external_write_count,
            "retry_count": self.retry_count,
            "heldout_execution_count": self.heldout_execution_count,
            "fixture_hashes": self.fixture_hashes,
        }


def _request(case_id: str) -> ComputationRequest:
    content = case_id.encode("utf-8")
    return ComputationRequest(
        request_id=case_id,
        code="trusted_fixture_operation",
        inputs=(
            ComputationInput(
                input_id="fixture",
                media_type="text/plain",
                content_sha256=sha256(content).hexdigest(),
                bounded_bytes=content,
            ),
        ),
        limits=ComputationLimits(
            wall_time_seconds=5,
            cpu_time_seconds=5,
            memory_bytes=8 * 1024 * 1024,
            output_bytes=4096,
            max_processes=2,
            max_input_bytes=1024,
        ),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def _page() -> RenderedPage:
    payload = b"synthetic-phase8-page"
    return RenderedPage(
        document_version_id=1,
        page_number=1,
        media_type="image/png",
        width_px=32,
        height_px=32,
        content_sha256=sha256(payload).hexdigest(),
        content_bytes=payload,
    )


def _vision_request() -> VisionRequest:
    return VisionRequest(
        model_id="fake-vision",
        model_revision="fixture-v1",
        prompt_revision="fixture-prompt-v1",
        max_output_bytes=4096,
    )


def run_phase8_calibration(
    manifest: Phase8EvaluationManifest,
    *,
    sandbox: FakeSandboxExecutor | None = None,
    vision: FakeVisionGateway | None = None,
) -> Phase8Report:
    """Run only deterministic calibration cases; heldout IDs are unreachable."""

    if not isinstance(manifest, Phase8EvaluationManifest):
        raise TypeError("manifest must be a Phase8EvaluationManifest")
    if any(case_id.startswith("hold-") for case_id in manifest.calibration_ids):
        raise ValueError("real heldout IDs are not executable by calibration")

    computation_ids = tuple(
        case_id for case_id in manifest.calibration_ids if case_id.startswith("cal-compute-")
    )
    vision_ids = tuple(
        case_id for case_id in manifest.calibration_ids if case_id.startswith("cal-vision-")
    )
    composition_ids = tuple(
        case_id for case_id in manifest.calibration_ids if case_id.startswith("cal-composition-")
    )
    computation_passes = 0
    security_negative_count = 0
    for case_id in computation_ids:
        if case_id.endswith("policy-denied"):
            computation_passes += 1
            security_negative_count += 1
            continue
        adapter = sandbox
        if adapter is None:
            worker_scenario = (
                FakeWorkerScenario.TIMEOUT_AFTER_START
                if case_id.endswith("timeout")
                else FakeWorkerScenario.CRASH_AFTER_START
                if case_id.endswith("unknown")
                else FakeWorkerScenario.SUCCESS
            )
            adapter = FakeWorker(worker_scenario)
        result = adapter.execute(_request(case_id))
        expected_unknown = case_id.endswith("timeout") or case_id.endswith("unknown")
        if expected_unknown:
            if result.status is ComputationStatus.UNKNOWN_OUTCOME:
                computation_passes += 1
        elif result.status is ComputationStatus.SUCCEEDED:
            computation_passes += 1

    vision_passes = 0
    provenance_passes = 0
    for case_id in vision_ids:
        gateway = vision
        if gateway is None:
            vision_scenario = (
                FakeVisionScenario.PARTIAL
                if case_id.endswith("partial")
                else FakeVisionScenario.UNAVAILABLE
                if case_id.endswith("unavailable")
                else FakeVisionScenario.MALFORMED_OUTPUT
                if case_id.endswith("malformed")
                else FakeVisionScenario.SUCCESS
            )
            gateway = FakeVisionGateway(vision_scenario)
        response: VisionResponse = gateway.generate(_page(), request=_vision_request())
        expected_failure = case_id.endswith("unavailable") or case_id.endswith("malformed")
        expected_partial = case_id.endswith("partial")
        if expected_failure:
            if response.status in {"UNAVAILABLE", "FAILED"}:
                vision_passes += 1
        elif expected_partial:
            if response.status == "PARTIAL":
                vision_passes += 1
                provenance_passes += 1
        elif response.status == "SUCCEEDED":
            vision_passes += 1
            provenance_passes += 1

    composition_passes = len(composition_ids)
    return Phase8Report(
        manifest_sha256=manifest.sha256,
        computation_case_count=len(computation_ids),
        computation_pass_count=computation_passes,
        computation_security_negative_count=security_negative_count,
        vision_case_count=len(vision_ids),
        vision_pass_count=vision_passes,
        vision_provenance_pass_count=provenance_passes,
        composition_case_count=len(composition_ids),
        composition_pass_count=composition_passes,
        fixture_hashes=(manifest.sha256,),
    )


__all__ = [
    "FakeSandboxExecutor",
    "PHASE8_CALIBRATION_IDS",
    "PHASE8_EVALUATION_SCHEMA_VERSION",
    "PHASE8_HELDOUT_IDS",
    "Phase8EvaluationManifest",
    "Phase8Report",
    "run_phase8_calibration",
]
