"""Trusted-core calibration runner and sanitized synthetic artifact retention."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .manifest import (
    EvaluationCategory,
    EvaluationInvariant,
    EvaluationSetup,
    FrozenEvaluationManifest,
    Phase6AEvaluationCase,
    build_calibration_cases,
    build_heldout_cases,
    freeze_manifest,
)


@dataclass(frozen=True, slots=True)
class SecurityProbeResult:
    executed: bool
    permission_changed: bool
    registry_changed: bool


MetricValue = int | float | bool


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    owner: str
    category: EvaluationCategory
    setup: EvaluationSetup
    expected_invariant: EvaluationInvariant
    passed: bool
    metrics: Mapping[str, MetricValue]
    security_probe: SecurityProbeResult | None = None

    def to_sanitized_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "case_id": self.case_id,
            "owner": self.owner,
            "category": self.category.value,
            "setup": self.setup.value,
            "expected_invariant": self.expected_invariant.value,
            "passed": self.passed,
            "metrics": dict(self.metrics),
        }
        if self.security_probe is not None:
            result["security_probe"] = {
                "executed": self.security_probe.executed,
                "permission_changed": self.security_probe.permission_changed,
                "registry_changed": self.security_probe.registry_changed,
            }
        return result


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    model_call_count: int
    rag_execution_count: int
    security_hard_gate_failures: int
    heldout_execution_count: int


class Phase6AHarness:
    """A deterministic fixture harness with no application-runtime authority."""

    def __init__(self) -> None:
        self.model_call_count = 0
        self.rag_execution_count = 0
        self.security_hard_gate_failures = 0

    def execute_case(self, case: Phase6AEvaluationCase) -> CaseResult:
        if case.case_id.startswith("hold-"):
            raise ValueError("heldout cases require the separately authorized CLI")
        security_probe = None
        if case.category is EvaluationCategory.SECURITY_IMPORT:
            security_probe = SecurityProbeResult(False, False, False)
        metrics: dict[str, MetricValue] = {
            "capability_approval": True,
            "trusted_validation": True,
            "claim_duplicate_prevention": True,
            "lifecycle_reconciliation": True,
            "security_hard_gate": True,
            "runtime_import": True,
            "latency_ms": 0.0,
        }
        return CaseResult(
            case.case_id,
            case.owner,
            case.category,
            case.setup,
            case.expected_invariant,
            True,
            metrics,
            security_probe,
        )


class RealHeldoutExecutor:
    """Task 11 seam; Task 10 never constructs or calls this executor."""

    def execute(self, case: Phase6AEvaluationCase) -> CaseResult:
        del case
        raise RuntimeError("real heldout execution is unavailable in Task 10")


def run_trusted_case(case: Phase6AEvaluationCase, harness: Phase6AHarness) -> CaseResult:
    if case.case_id.startswith("hold-"):
        raise ValueError("heldout cases cannot run through the trusted calibration harness")
    return harness.execute_case(case)


def _run_calibration_cases(
    cases: Sequence[Phase6AEvaluationCase],
) -> tuple[EvaluationRunResult, tuple[CaseResult, ...]]:
    harness = Phase6AHarness()
    results = tuple(run_trusted_case(case, harness) for case in cases)
    return (
        EvaluationRunResult(
            model_call_count=harness.model_call_count,
            rag_execution_count=harness.rag_execution_count,
            security_hard_gate_failures=harness.security_hard_gate_failures,
            heldout_execution_count=0,
        ),
        results,
    )


def run_manifest(cases: Sequence[Phase6AEvaluationCase]) -> EvaluationRunResult:
    """Run synthetic calibration cases only; reject any heldout input explicitly."""

    if any(case.case_id.startswith("hold-") for case in cases):
        raise ValueError("heldout cases are CLI-only and cannot be consumed by pytest")
    run, _ = _run_calibration_cases(tuple(cases))
    return run


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    return hashlib.sha256((encoded + "\n").encode("utf-8")).hexdigest()


def _write_manifest(manifest: FrozenEvaluationManifest, path: Path) -> str:
    return _write_json(path, manifest.to_dict())


def _sanitized_summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for result in results:
        categories[result.category.value] = categories.get(result.category.value, 0) + 1
    return {
        "schema_version": "phase6a-trusted-core-report-v1",
        "case_count": len(results),
        "category_counts": categories,
        "MODEL_CALL_COUNT": 0,
        "RAG_EXECUTION_COUNT": 0,
        "metrics": {
            "capability_approval": "separate_gate",
            "trusted_validation": "separate_gate",
            "claim_duplicate_prevention": "separate_gate",
            "lifecycle_reconciliation": "separate_gate",
            "security_hard_gate": "separate_gate",
            "runtime_import": "separate_gate",
            "supplementary_latency": "diagnostic_only",
        },
        "results": [result.to_sanitized_dict() for result in results],
    }


def write_sanitized_report(results: Sequence[CaseResult], path: Path) -> str:
    if any(result.case_id.startswith("hold-") for result in results):
        raise ValueError("Task 10 reports may contain calibration cases only")
    return _write_json(path, _sanitized_summary(results))


def write_private_bundle(results: Sequence[CaseResult], path: Path) -> str:
    if any(result.case_id.startswith("hold-") for result in results):
        raise ValueError("Task 10 bundles may contain calibration cases only")
    bundle = {
        "schema_version": "phase6a-trusted-core-private-v1",
        "evidence": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "category": result.category.value,
                "metrics": dict(result.metrics),
            }
            for result in results
        ],
    }
    return _write_json(path, bundle)


def run_calibration_only(manifest_path: Path) -> EvaluationRunResult:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("manifest JSON must be an object")
    manifest = FrozenEvaluationManifest.from_dict(raw)
    if any(case.case_id.startswith("hold-") for case in manifest.cases):
        raise ValueError("calibration path cannot consume heldout cases")
    return run_manifest(manifest.cases)


def run_calibration_and_retain_synthetic_artifacts(output_dir: Path) -> tuple[str, str]:
    calibration = freeze_manifest(build_calibration_cases(), version="phase6a-trusted-core-v1")
    heldout = freeze_manifest(build_heldout_cases(), version="phase6a-trusted-core-v1")
    manifest_dir = output_dir / "manifests"
    _write_manifest(calibration, manifest_dir / "phase6a_calibration_v1.json")
    _write_manifest(heldout, manifest_dir / "phase6a_heldout_v1.json")
    _, results = _run_calibration_cases(calibration.cases)
    report_hash = write_sanitized_report(
        results, output_dir / "reports" / "phase6a_calibration_v1.json"
    )
    bundle_hash = write_private_bundle(
        results, output_dir / "private" / "phase6a_calibration_v1.bundle"
    )
    return report_hash, bundle_hash


def artifact_tree_contains_case_ids(output_dir: Path, *, prefix: str) -> bool:
    for path in output_dir.rglob("*"):
        # The frozen heldout manifest is intentionally retained beside the
        # calibration manifest; this helper checks retained result artifacts,
        # not the predeclared membership file.
        if "manifests" in path.parts:
            continue
        if path.is_file() and (prefix in path.name or prefix in path.read_text(encoding="utf-8")):
            return True
    return False


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Phase 6A calibration fixtures")
    parser.add_argument("--split", choices=("calibration",), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    result = run_calibration_only(args.manifest)
    print(json.dumps({
        "MODEL_CALL_COUNT": result.model_call_count,
        "RAG_EXECUTION_COUNT": result.rag_execution_count,
        "SECURITY_HARD_GATE_FAILURES": result.security_hard_gate_failures,
        "HELDOUT_EXECUTION_COUNT": result.heldout_execution_count,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
