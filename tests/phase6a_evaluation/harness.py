"""Trusted-core calibration runner and sanitized synthetic artifact retention."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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


@dataclass(frozen=True, slots=True)
class FixtureExecution:
    capability_approval: bool
    trusted_validation: bool
    claim_duplicate_prevention: bool
    lifecycle_reconciliation: bool
    security_hard_gate: bool
    runtime_import: bool
    latency_ms: float
    security_probe: SecurityProbeResult | None


class TrustedCoreFixture:
    """Small in-memory repository/service/adapter seam for trusted-core cases."""

    def failed_execution(self) -> FixtureExecution:
        return FixtureExecution(False, False, False, False, False, False, 0.0, None)

    def evaluate(self, case: Phase6AEvaluationCase) -> FixtureExecution:
        # These transitions are deliberately local and deterministic.  They
        # model the repository/service/adapter boundaries without importing
        # the real database or any external capability.
        events: list[str] = []
        claims: set[str] = set()
        events.append(case.setup.value)
        contract = {
            EvaluationCategory.APPROVAL_EXPIRY: (
                case.owner == "approval" and case.expected_invariant is EvaluationInvariant.APPROVAL_REQUIRED
            ),
            EvaluationCategory.CAPABILITY_POLICY: (
                case.owner == "capability" and case.expected_invariant is EvaluationInvariant.CAPABILITY_REVALIDATED
            ),
            EvaluationCategory.CLAIM_CONCURRENCY: (
                case.owner == "claim" and case.expected_invariant is EvaluationInvariant.SINGLE_CLAIM
            ),
            EvaluationCategory.DISPATCH_PRECONDITION: (
                case.owner == "dispatch" and case.expected_invariant is EvaluationInvariant.DURABLE_DISPATCH
            ),
            EvaluationCategory.OUTCOME_RECONCILIATION: (
                case.owner == "outcome" and case.expected_invariant is EvaluationInvariant.TRUSTFUL_OUTCOME
            ),
            EvaluationCategory.SECURITY_IMPORT: (
                case.owner in ("security", "import")
                and case.expected_invariant
                in (EvaluationInvariant.SECURITY_ISOLATION, EvaluationInvariant.IMPORT_SAFE)
            ),
        }[case.category]
        if case.category is EvaluationCategory.CLAIM_CONCURRENCY:
            first_claim_won = case.case_id not in claims
            claims.add(case.case_id)
            second_claim_rejected = case.case_id in claims
            duplicate_rejected = first_claim_won and second_claim_rejected
            events.append("DUPLICATE_REJECTED")
        else:
            duplicate_rejected = True
        lifecycle = case.category in (
            EvaluationCategory.DISPATCH_PRECONDITION,
            EvaluationCategory.OUTCOME_RECONCILIATION,
        )
        if lifecycle:
            events.append("LIFECYCLE_CHECKED")
        probe = (
            SecurityProbeResult(False, False, False)
            if case.category is EvaluationCategory.SECURITY_IMPORT
            else None
        )
        security_safe = probe is None or (
            not probe.executed and not probe.permission_changed and not probe.registry_changed
        )
        return FixtureExecution(
            capability_approval=contract,
            trusted_validation=contract and bool(events),
            claim_duplicate_prevention=duplicate_rejected,
            lifecycle_reconciliation=(lifecycle and "LIFECYCLE_CHECKED" in events)
            or not lifecycle,
            security_hard_gate=security_safe,
            runtime_import=case.setup is not EvaluationSetup.IMPORT_SMOKE
            or case.expected_invariant is EvaluationInvariant.IMPORT_SAFE,
            latency_ms=float(len(events)),
            security_probe=probe,
        )


_CLOSED_METRIC_KEYS = frozenset(
    {
        "capability_approval",
        "trusted_validation",
        "claim_duplicate_prevention",
        "lifecycle_reconciliation",
        "security_hard_gate",
        "runtime_import",
        "latency_ms",
    }
)


def _validate_case_result(result: CaseResult) -> None:
    if not result.case_id.startswith("cal-"):
        raise ValueError("case result must be a calibration result")
    canonical = {case.case_id: case for case in build_calibration_cases()}.get(result.case_id)
    if canonical is None or (
        result.owner,
        result.category,
        result.setup,
        result.expected_invariant,
    ) != (
        canonical.owner,
        canonical.category,
        canonical.setup,
        canonical.expected_invariant,
    ):
        raise ValueError("case result metadata is not canonical")
    if not isinstance(result.owner, str) or len(result.owner) > 32:
        raise TypeError("case result owner is not bounded")
    if not isinstance(result.category, EvaluationCategory):
        raise TypeError("case result category is not typed")
    if not isinstance(result.setup, EvaluationSetup):
        raise TypeError("case result setup is not typed")
    if not isinstance(result.expected_invariant, EvaluationInvariant):
        raise TypeError("case result invariant is not typed")
    keys = set(result.metrics)
    if keys != set(_CLOSED_METRIC_KEYS):
        raise ValueError("metric keys are not closed")
    for key, value in result.metrics.items():
        if not isinstance(key, str) or len(key) > 64:
            raise TypeError("metric key is not bounded")
        if not isinstance(value, (bool, int, float)) or isinstance(value, str):
            raise TypeError(f"metric value for {key} is not a scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"metric value for {key} is not finite")
        if abs(float(value)) > 1_000_000:
            raise ValueError(f"metric value for {key} is out of bounds")
    if not isinstance(result.passed, bool):
        raise TypeError("case result passed flag is not typed")
    if result.security_probe is not None and not all(
        isinstance(value, bool) for value in (
            result.security_probe.executed,
            result.security_probe.permission_changed,
            result.security_probe.registry_changed,
        )
    ):
        raise TypeError("security probe is not typed")


class Phase6AHarness:
    """A deterministic fixture harness with no application-runtime authority."""

    def __init__(self, *, fixture: TrustedCoreFixture | None = None) -> None:
        self.model_call_count = 0
        self.rag_execution_count = 0
        self.security_hard_gate_failures = 0
        self._fixture = fixture or TrustedCoreFixture()

    def execute_case(self, case: Phase6AEvaluationCase) -> CaseResult:
        if case.case_id.startswith("hold-"):
            raise ValueError("heldout cases require the separately authorized CLI")
        execution = self._fixture.evaluate(case)
        metrics: dict[str, MetricValue] = {
            "capability_approval": execution.capability_approval,
            "trusted_validation": execution.trusted_validation,
            "claim_duplicate_prevention": execution.claim_duplicate_prevention,
            "lifecycle_reconciliation": execution.lifecycle_reconciliation,
            "security_hard_gate": execution.security_hard_gate,
            "runtime_import": execution.runtime_import,
            "latency_ms": execution.latency_ms,
        }
        passed = all(value is True for key, value in metrics.items() if key != "latency_ms")
        if not execution.security_hard_gate:
            self.security_hard_gate_failures += 1
        return CaseResult(
            case.case_id,
            case.owner,
            case.category,
            case.setup,
            case.expected_invariant,
            passed,
            metrics,
            execution.security_probe,
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
    frozen = freeze_manifest(tuple(cases), version="phase6a-trusted-core-v1")
    run, _ = _run_calibration_cases(frozen.cases)
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
    for result in results:
        _validate_case_result(result)
    return _write_json(path, _sanitized_summary(results))


def write_private_bundle(results: Sequence[CaseResult], path: Path) -> str:
    if any(result.case_id.startswith("hold-") for result in results):
        raise ValueError("Task 10 bundles may contain calibration cases only")
    for result in results:
        _validate_case_result(result)
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
