"""Bounded deterministic Phase 8 acceptance report assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .evaluation import (
    PHASE8_CALIBRATION_IDS,
    PHASE8_HELDOUT_IDS,
    Phase8EvaluationManifest,
    run_phase8_calibration,
)

PHASE8_ACCEPTANCE_SCHEMA_VERSION = "phase8-acceptance-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Phase8AcceptanceReport:
    """Synthetic trusted-core result; no provider or heldout execution path."""

    schema_version: str
    manifest_sha256: str
    computation_case_count: int
    computation_pass_count: int
    vision_case_count: int
    vision_pass_count: int
    composition_case_count: int
    composition_pass_count: int
    security_negative_pass_count: int
    model_call_count: int
    rag_execution_count: int
    external_write_count: int
    retry_count: int
    heldout_execution_count: int
    phase6_bypass_count: int
    browser_execution_route_count: int
    rlm_work: bool

    def __post_init__(self) -> None:
        if self.schema_version != PHASE8_ACCEPTANCE_SCHEMA_VERSION:
            raise ValueError("unsupported Phase 8 acceptance schema")
        if not _SHA256.fullmatch(self.manifest_sha256):
            raise ValueError("manifest_sha256 must be lowercase SHA-256")
        for name in (
            "computation_case_count",
            "computation_pass_count",
            "vision_case_count",
            "vision_pass_count",
            "composition_case_count",
            "composition_pass_count",
            "security_negative_pass_count",
            "model_call_count",
            "rag_execution_count",
            "external_write_count",
            "retry_count",
            "heldout_execution_count",
            "phase6_bypass_count",
            "browser_execution_route_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.rlm_work, bool):
            raise TypeError("rlm_work must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_sha256": self.manifest_sha256,
            "computation_case_count": self.computation_case_count,
            "computation_pass_count": self.computation_pass_count,
            "vision_case_count": self.vision_case_count,
            "vision_pass_count": self.vision_pass_count,
            "composition_case_count": self.composition_case_count,
            "composition_pass_count": self.composition_pass_count,
            "security_negative_pass_count": self.security_negative_pass_count,
            "model_call_count": self.model_call_count,
            "rag_execution_count": self.rag_execution_count,
            "external_write_count": self.external_write_count,
            "retry_count": self.retry_count,
            "heldout_execution_count": self.heldout_execution_count,
            "phase6_bypass_count": self.phase6_bypass_count,
            "browser_execution_route_count": self.browser_execution_route_count,
            "rlm_work": self.rlm_work,
        }


def run_phase8_acceptance(**kwargs: object) -> Phase8AcceptanceReport:
    """Assemble deterministic acceptance evidence without side effects."""

    if kwargs:
        raise TypeError("run_phase8_acceptance accepts no arbitrary arguments")
    manifest = Phase8EvaluationManifest.from_ids(PHASE8_CALIBRATION_IDS, PHASE8_HELDOUT_IDS)
    calibration = run_phase8_calibration(manifest)
    return Phase8AcceptanceReport(
        schema_version=PHASE8_ACCEPTANCE_SCHEMA_VERSION,
        manifest_sha256=manifest.sha256,
        computation_case_count=calibration.computation_case_count,
        computation_pass_count=calibration.computation_pass_count,
        vision_case_count=calibration.vision_case_count,
        vision_pass_count=calibration.vision_pass_count,
        composition_case_count=calibration.composition_case_count,
        composition_pass_count=calibration.composition_pass_count,
        security_negative_pass_count=calibration.computation_security_negative_count,
        model_call_count=calibration.model_call_count,
        rag_execution_count=calibration.rag_execution_count,
        external_write_count=calibration.external_write_count,
        retry_count=calibration.retry_count,
        heldout_execution_count=calibration.heldout_execution_count,
        phase6_bypass_count=0,
        browser_execution_route_count=0,
        rlm_work=False,
    )


__all__ = [
    "PHASE8_ACCEPTANCE_SCHEMA_VERSION",
    "Phase8AcceptanceReport",
    "run_phase8_acceptance",
]
