"""Sealed, content-free V4 evaluation orchestration and aggregate contracts."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generic, TypeVar, cast

from aaos.agent.evaluation.contracts import EvaluationSplit
from aaos.agent.evaluation.v4 import (
    V4EvaluationManifest,
    V4EvaluationMetrics,
    V4EvaluationTrace,
    score_v4_evaluation,
)
from aaos.agent.evaluation.v4_ledger import (
    HeldoutLedgerError,
    V4HeldoutLedger,
)

AGGREGATE_SCHEMA_VERSION = "phase5-v4-safe-aggregate-v1"
RUNNER_VERSION = "phase5-v4-runner-v1"
MANIFEST_VERIFIER_ID = (
    "aaos.agent.evaluation.v4.build_v4_manifests+aaos.agent.evaluation.v4.V4EvaluationManifest"
)
SCORER_ID = "aaos.agent.evaluation.v4.score_v4_evaluation"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SAFE_STRATUM = re.compile(r"^[A-Za-z0-9_./-]{1,64}$")
_AGGREGATE_KEYS = {
    "aggregate_schema_version",
    "arbitrary_tool_execution_count",
    "benchmark_version",
    "candidate_sha",
    "case_count",
    "credential_exposure_count",
    "evaluation_kind",
    "execution_count",
    "fingerprint_valid",
    "global_routing_accuracy",
    "invalid_output_count",
    "lifecycle_violation_count",
    "manifest_valid",
    "model_call_count",
    "parse_failure_count",
    "passed",
    "per_stratum_accuracy",
    "policy_violation_count",
    "rag_execution_count",
    "routing_case_count",
    "runtime_failure_count",
    "model_unavailable_count",
    "model_timeout_count",
    "security_hard_gate_failure_count",
    "schema_failure_count",
    "split_disjoint",
    "strata_counts",
    "terminal_kind_error_count",
    "threshold",
    "trusted_system_case_count",
    "trusted_system_pass_count",
    "unauthorized_write_count",
}


T = TypeVar("T")


class V4RunnerError(RuntimeError):
    """A public-safe runner failure code."""

    def __init__(self, code: str) -> None:
        if code not in {
            "evaluation_failed",
            "preflight_failed",
            "manifest_failed",
            "report_write_failed",
        }:
            code = "evaluation_failed"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ContainedEvaluationOutput(Generic[T]):
    ok: bool
    value: T | None
    public_text: str
    error: V4RunnerError | HeldoutLedgerError | None = None


class _DiscardingTextIO(io.TextIOBase):
    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None


def contain_evaluation_output(operation: Callable[[], T]) -> ContainedEvaluationOutput[T]:
    """Run a callback while discarding its stdout/stderr and sanitizing exceptions."""

    sink = _DiscardingTextIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = operation()
    except (V4RunnerError, HeldoutLedgerError) as exc:
        return ContainedEvaluationOutput(False, None, '{"error":"evaluation_failed"}', error=exc)
    except Exception:
        return ContainedEvaluationOutput(False, None, '{"error":"evaluation_failed"}')
    return ContainedEvaluationOutput(True, result, '{"status":"complete"}')


@dataclass(frozen=True, slots=True)
class V4ExecutionCounters:
    invalid_output_count: int = 0
    schema_failure_count: int = 0
    parse_failure_count: int = 0
    unauthorized_write_count: int = 0
    arbitrary_tool_execution_count: int = 0
    credential_exposure_count: int = 0
    lifecycle_violation_count: int = 0
    policy_violation_count: int = 0
    model_call_count: int | None = None
    rag_execution_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "invalid_output_count",
            "schema_failure_count",
            "parse_failure_count",
            "unauthorized_write_count",
            "arbitrary_tool_execution_count",
            "credential_exposure_count",
            "lifecycle_violation_count",
            "policy_violation_count",
            "rag_execution_count",
        ):
            _nonnegative_int(getattr(self, name), name)
        if self.model_call_count is not None:
            _nonnegative_int(self.model_call_count, "model_call_count")
        if self.invalid_output_count != self.schema_failure_count + self.parse_failure_count:
            raise ValueError("invalid output diagnostics must be disjoint subtotals")


@dataclass(frozen=True, slots=True)
class V4ExecutionBatch:
    traces: tuple[V4EvaluationTrace, ...]
    counters: V4ExecutionCounters = field(default_factory=V4ExecutionCounters)
    fake_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.traces, tuple) or not all(
            isinstance(trace, V4EvaluationTrace) for trace in self.traces
        ):
            raise ValueError("execution traces are invalid")
        if not isinstance(self.counters, V4ExecutionCounters):
            raise ValueError("execution counters are invalid")
        if not isinstance(self.fake_only, bool):
            raise ValueError("execution boundary must be explicit")


@dataclass(frozen=True, slots=True)
class V4SafeAggregate:
    benchmark_version: str
    evaluation_kind: str
    candidate_sha: str
    case_count: int
    routing_case_count: int
    trusted_system_case_count: int
    trusted_system_pass_count: int | None
    security_hard_gate_failure_count: int | None
    runtime_failure_count: int | None
    model_unavailable_count: int | None
    model_timeout_count: int | None
    strata_counts: tuple[tuple[str, int], ...]
    global_routing_accuracy: float | None
    per_stratum_accuracy: tuple[tuple[str, float | None], ...]
    invalid_output_count: int
    terminal_kind_error_count: int
    schema_failure_count: int
    parse_failure_count: int
    unauthorized_write_count: int
    arbitrary_tool_execution_count: int
    credential_exposure_count: int
    lifecycle_violation_count: int
    policy_violation_count: int
    model_call_count: int
    rag_execution_count: int
    manifest_valid: bool
    split_disjoint: bool
    fingerprint_valid: bool
    threshold: float
    passed: bool
    execution_count: int
    aggregate_schema_version: str = AGGREGATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_version, str) or not _SAFE_VERSION.fullmatch(
            self.benchmark_version
        ):
            raise ValueError("aggregate schema does not match")
        if self.evaluation_kind not in {"manifest", "calibration", "heldout"}:
            raise ValueError("aggregate schema does not match")
        if not isinstance(self.candidate_sha, str) or not _SHA1.fullmatch(self.candidate_sha):
            raise ValueError("aggregate schema does not match")
        _nonnegative_int(self.case_count, "case_count")
        _nonnegative_int(self.routing_case_count, "routing_case_count")
        _nonnegative_int(self.trusted_system_case_count, "trusted_system_case_count")
        if self.routing_case_count + self.trusted_system_case_count > self.case_count:
            raise ValueError("aggregate schema does not match")
        for name in (
            "trusted_system_pass_count",
            "security_hard_gate_failure_count",
            "runtime_failure_count",
            "model_unavailable_count",
            "model_timeout_count",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_int(value, name)
        if (
            self.trusted_system_pass_count is not None
            and self.trusted_system_pass_count > self.trusted_system_case_count
        ):
            raise ValueError("aggregate schema does not match")
        evaluation_metrics = (
            self.trusted_system_pass_count,
            self.security_hard_gate_failure_count,
            self.runtime_failure_count,
            self.model_unavailable_count,
            self.model_timeout_count,
        )
        if self.evaluation_kind == "manifest":
            if any(value is not None for value in evaluation_metrics):
                raise ValueError("aggregate schema does not match")
        elif any(value is None for value in evaluation_metrics):
            raise ValueError("aggregate schema does not match")
        for name in (
            "invalid_output_count",
            "terminal_kind_error_count",
            "schema_failure_count",
            "parse_failure_count",
            "unauthorized_write_count",
            "arbitrary_tool_execution_count",
            "credential_exposure_count",
            "lifecycle_violation_count",
            "policy_violation_count",
            "model_call_count",
            "rag_execution_count",
            "execution_count",
        ):
            _nonnegative_int(getattr(self, name), name)
        if self.invalid_output_count != self.schema_failure_count + self.parse_failure_count:
            raise ValueError("aggregate schema does not match")
        if self.execution_count not in {0, 1}:
            raise ValueError("aggregate schema does not match")
        for value in (
            self.manifest_valid,
            self.split_disjoint,
            self.fingerprint_valid,
            self.passed,
        ):
            if not isinstance(value, bool):
                raise ValueError("aggregate schema does not match")
        _ratio(self.threshold, "threshold")
        if self.global_routing_accuracy is not None:
            _ratio(self.global_routing_accuracy, "global_routing_accuracy")
        if self.aggregate_schema_version != AGGREGATE_SCHEMA_VERSION:
            raise ValueError("aggregate schema does not match")
        if not isinstance(self.strata_counts, tuple) or not isinstance(
            self.per_stratum_accuracy, tuple
        ):
            raise ValueError("aggregate schema does not match")
        seen: set[str] = set()
        for key, count in self.strata_counts:
            if not isinstance(key, str) or not _SAFE_STRATUM.fullmatch(key) or key in seen:
                raise ValueError("aggregate schema does not match")
            seen.add(key)
            _nonnegative_int(count, "stratum_count")
        if sum(count for _, count in self.strata_counts) != self.routing_case_count:
            raise ValueError("aggregate schema does not match")
        seen.clear()
        for key, accuracy_value in self.per_stratum_accuracy:
            if not isinstance(key, str) or not _SAFE_STRATUM.fullmatch(key) or key in seen:
                raise ValueError("aggregate schema does not match")
            seen.add(key)
            if accuracy_value is not None:
                _ratio(accuracy_value, "per_stratum_accuracy")

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregate_schema_version": self.aggregate_schema_version,
            "arbitrary_tool_execution_count": self.arbitrary_tool_execution_count,
            "benchmark_version": self.benchmark_version,
            "candidate_sha": self.candidate_sha,
            "case_count": self.case_count,
            "credential_exposure_count": self.credential_exposure_count,
            "evaluation_kind": self.evaluation_kind,
            "execution_count": self.execution_count,
            "fingerprint_valid": self.fingerprint_valid,
            "global_routing_accuracy": self.global_routing_accuracy,
            "invalid_output_count": self.invalid_output_count,
            "lifecycle_violation_count": self.lifecycle_violation_count,
            "manifest_valid": self.manifest_valid,
            "model_call_count": self.model_call_count,
            "model_timeout_count": self.model_timeout_count,
            "model_unavailable_count": self.model_unavailable_count,
            "parse_failure_count": self.parse_failure_count,
            "passed": self.passed,
            "per_stratum_accuracy": [
                {"accuracy": value, "stratum": key} for key, value in self.per_stratum_accuracy
            ],
            "policy_violation_count": self.policy_violation_count,
            "rag_execution_count": self.rag_execution_count,
            "routing_case_count": self.routing_case_count,
            "runtime_failure_count": self.runtime_failure_count,
            "schema_failure_count": self.schema_failure_count,
            "security_hard_gate_failure_count": self.security_hard_gate_failure_count,
            "split_disjoint": self.split_disjoint,
            "strata_counts": [
                {"count": count, "stratum": key} for key, count in self.strata_counts
            ],
            "terminal_kind_error_count": self.terminal_kind_error_count,
            "threshold": self.threshold,
            "trusted_system_case_count": self.trusted_system_case_count,
            "trusted_system_pass_count": self.trusted_system_pass_count,
            "unauthorized_write_count": self.unauthorized_write_count,
        }

    def to_json(self) -> str:
        payload = json.dumps(
            self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        if len(payload) > 16_384:
            raise ValueError("aggregate exceeds the public output bound")
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> V4SafeAggregate:
        if not isinstance(payload, dict) or set(payload) != _AGGREGATE_KEYS:
            raise ValueError("aggregate schema does not match")
        try:
            strata_counts = tuple(
                (item["stratum"], item["count"])
                for item in _strict_list_of_dicts(payload["strata_counts"], {"count", "stratum"})
            )
            per_stratum_accuracy = tuple(
                (item["stratum"], item["accuracy"])
                for item in _strict_list_of_dicts(
                    payload["per_stratum_accuracy"], {"accuracy", "stratum"}
                )
            )
            values = dict(payload)
            values["strata_counts"] = strata_counts
            values["per_stratum_accuracy"] = per_stratum_accuracy
            values["aggregate_schema_version"] = payload["aggregate_schema_version"]
            return cls(**values)
        except (KeyError, TypeError, ValueError):
            raise ValueError("aggregate schema does not match") from None


@dataclass(frozen=True, slots=True)
class _ManifestPair:
    calibration: V4EvaluationManifest
    heldout: V4EvaluationManifest
    manifest_valid: bool
    split_disjoint: bool
    fingerprint_valid: bool


class V4SealedRunner:
    """Run manifest, calibration, and one-time held-out through injected ports."""

    def __init__(
        self,
        *,
        manifest_factory: Callable[[], tuple[V4EvaluationManifest, V4EvaluationManifest]],
        scorer: Callable[
            [V4EvaluationManifest, tuple[V4EvaluationTrace, ...]], V4EvaluationMetrics
        ] = score_v4_evaluation,
        executor: Callable[
            [V4EvaluationManifest], V4ExecutionBatch | tuple[V4EvaluationTrace, ...]
        ],
        candidate_sha: str,
        ledger: V4HeldoutLedger,
        report_root: Path | str,
        preflight: Callable[[], None] = lambda: None,
        fake_only: bool = True,
    ) -> None:
        if not isinstance(candidate_sha, str) or not _SHA1.fullmatch(candidate_sha):
            raise ValueError("candidate identity is invalid")
        if not callable(manifest_factory) or not callable(scorer) or not callable(executor):
            raise ValueError("runner dependency is invalid")
        if not isinstance(ledger, V4HeldoutLedger):
            raise ValueError("held-out ledger is invalid")
        if not callable(preflight):
            raise ValueError("preflight dependency is invalid")
        if fake_only is not True:
            raise ValueError("V4 execution is restricted to the fake-only boundary")
        self._manifest_factory = manifest_factory
        self._scorer = scorer
        self._executor = executor
        self._candidate_sha = candidate_sha
        self._ledger = ledger
        self._report_root = Path(report_root)
        self._preflight = preflight

    def verify_manifest(self) -> V4SafeAggregate:
        return self._sealed(self._verify_manifest)

    def calibrate(self) -> V4SafeAggregate:
        return self._sealed(self._calibrate)

    def evaluate_heldout(self) -> V4SafeAggregate:
        return self._sealed(self._evaluate_heldout)

    def ledger_status(self) -> dict[str, object]:
        return self._sealed(lambda: self._ledger.status().to_dict())

    def _verify_manifest(self) -> V4SafeAggregate:
        pair = self._load_pair()
        report = V4SafeAggregate(
            benchmark_version=pair.calibration.benchmark_version,
            evaluation_kind="manifest",
            candidate_sha=self._candidate_sha,
            case_count=len(pair.calibration.cases) + len(pair.heldout.cases),
            routing_case_count=sum(count for _, count in self._paired_strata_counts(pair)),
            trusted_system_case_count=(
                len(pair.calibration.trusted_cases) + len(pair.heldout.trusted_cases)
            ),
            trusted_system_pass_count=None,
            security_hard_gate_failure_count=None,
            runtime_failure_count=None,
            model_unavailable_count=None,
            model_timeout_count=None,
            strata_counts=self._paired_strata_counts(pair),
            global_routing_accuracy=None,
            per_stratum_accuracy=(),
            invalid_output_count=0,
            terminal_kind_error_count=0,
            schema_failure_count=0,
            parse_failure_count=0,
            unauthorized_write_count=0,
            arbitrary_tool_execution_count=0,
            credential_exposure_count=0,
            lifecycle_violation_count=0,
            policy_violation_count=0,
            model_call_count=0,
            rag_execution_count=0,
            manifest_valid=pair.manifest_valid,
            split_disjoint=pair.split_disjoint,
            fingerprint_valid=pair.fingerprint_valid,
            threshold=float(pair.calibration.routing_accuracy_target),
            passed=pair.manifest_valid and pair.split_disjoint and pair.fingerprint_valid,
            execution_count=0,
        )
        self._write_report(report)
        return report

    def _calibrate(self) -> V4SafeAggregate:
        try:
            self._preflight()
        except Exception:
            raise V4RunnerError("preflight_failed") from None
        pair = self._load_pair()
        batch = self._execute(pair.calibration)
        report = self._score_report(
            pair,
            pair.calibration,
            batch,
            evaluation_kind="calibration",
            execution_count=0,
        )
        self._write_report(report)
        return report

    def _evaluate_heldout(self) -> V4SafeAggregate:
        reservation = self._ledger._reserve_after_preflight(
            self._candidate_sha,
            self._preflight,
        )
        if reservation is None:
            raise V4RunnerError("preflight_failed") from None

        self._ledger.consume(reservation.reservation_id)
        try:
            pair = self._load_pair()
            batch = self._execute(pair.heldout)
            report = self._score_report(
                pair,
                pair.heldout,
                batch,
                evaluation_kind="heldout",
                execution_count=1,
            )
            self._write_report(report)
            report_hash = hashlib.sha256(report.to_json().encode("utf-8")).hexdigest()
            self._ledger.finish(
                reservation.reservation_id,
                completion_status="complete",
                aggregate_report_sha256=report_hash,
            )
            return report
        except Exception:
            try:
                self._ledger.finish(
                    reservation.reservation_id,
                    completion_status="failed",
                    aggregate_report_sha256=None,
                )
            except HeldoutLedgerError as ledger_error:
                raise V4RunnerError("evaluation_failed") from ledger_error
            raise V4RunnerError("evaluation_failed") from None

    def _load_pair(self) -> _ManifestPair:
        try:
            manifests = self._manifest_factory()
            if (
                not isinstance(manifests, tuple)
                or len(manifests) != 2
                or not all(isinstance(item, V4EvaluationManifest) for item in manifests)
            ):
                raise ValueError
            calibration, heldout = manifests
            if (
                calibration.split is not EvaluationSplit.CALIBRATION
                or heldout.split is not EvaluationSplit.FINAL_HELDOUT
            ):
                raise ValueError
            if calibration.benchmark_version != heldout.benchmark_version:
                raise ValueError
            content_duplicates = calibration.routing_content_duplicate_count(heldout)
            split_disjoint = content_duplicates == 0 and not (
                set(calibration.case_ids) & set(heldout.case_ids)
            )
            fingerprint_valid = all(
                _SHA256.fullmatch(digest)
                for digest in (
                    calibration.schema_fingerprint,
                    calibration.dataset_fingerprint,
                    calibration.split_fingerprint,
                    heldout.schema_fingerprint,
                    heldout.dataset_fingerprint,
                    heldout.split_fingerprint,
                )
            )
            manifest_valid = all(
                manifest.routing_case_count >= manifest.routing_minimum
                and manifest.routing_coverage_valid
                for manifest in (calibration, heldout)
            )
            return _ManifestPair(
                calibration,
                heldout,
                manifest_valid,
                split_disjoint,
                fingerprint_valid,
            )
        except V4RunnerError:
            raise
        except Exception:
            raise V4RunnerError("manifest_failed") from None

    def _execute(self, manifest: V4EvaluationManifest) -> V4ExecutionBatch:
        try:
            result = self._executor(manifest)
            batch = result if isinstance(result, V4ExecutionBatch) else V4ExecutionBatch(result)
            if not batch.fake_only:
                raise ValueError
            if batch.counters.rag_execution_count != 0:
                raise ValueError
            return batch
        except Exception:
            raise V4RunnerError("evaluation_failed") from None

    def _score_report(
        self,
        pair: _ManifestPair,
        manifest: V4EvaluationManifest,
        batch: V4ExecutionBatch,
        *,
        evaluation_kind: str,
        execution_count: int,
    ) -> V4SafeAggregate:
        try:
            metrics = self._scorer(manifest, batch.traces)
            routing = metrics.model_routing
            efficiency = metrics.proposal_format_efficiency
            model_calls = (
                batch.counters.model_call_count
                if batch.counters.model_call_count is not None
                else efficiency.model_calls
            )
            terminal_kind_errors = self._terminal_kind_errors(manifest, batch.traces)
            passed = (
                pair.manifest_valid
                and pair.split_disjoint
                and pair.fingerprint_valid
                and metrics.formal_acceptance_eligible
                and batch.counters.unauthorized_write_count == 0
                and batch.counters.arbitrary_tool_execution_count == 0
                and batch.counters.credential_exposure_count == 0
                and batch.counters.lifecycle_violation_count == 0
                and batch.counters.policy_violation_count == 0
                and batch.counters.rag_execution_count == 0
            )
            return V4SafeAggregate(
                benchmark_version=manifest.benchmark_version,
                evaluation_kind=evaluation_kind,
                candidate_sha=self._candidate_sha,
                case_count=len(manifest.cases),
                routing_case_count=routing.total,
                trusted_system_case_count=metrics.trusted_system.total,
                trusted_system_pass_count=metrics.trusted_system.correct,
                security_hard_gate_failure_count=metrics.security_hard_gates.failure_count,
                runtime_failure_count=metrics.runtime_environment.runtime_failures,
                model_unavailable_count=metrics.runtime_environment.model_unavailable,
                model_timeout_count=metrics.runtime_environment.model_timeout,
                strata_counts=tuple(routing.stratum_totals),
                global_routing_accuracy=routing.accuracy.ratio,
                per_stratum_accuracy=self._stratum_accuracies(
                    routing.stratum_totals, routing.stratum_correct
                ),
                invalid_output_count=batch.counters.invalid_output_count,
                terminal_kind_error_count=terminal_kind_errors,
                schema_failure_count=batch.counters.schema_failure_count,
                parse_failure_count=batch.counters.parse_failure_count,
                unauthorized_write_count=batch.counters.unauthorized_write_count,
                arbitrary_tool_execution_count=batch.counters.arbitrary_tool_execution_count,
                credential_exposure_count=batch.counters.credential_exposure_count,
                lifecycle_violation_count=batch.counters.lifecycle_violation_count,
                policy_violation_count=batch.counters.policy_violation_count,
                model_call_count=model_calls,
                rag_execution_count=batch.counters.rag_execution_count,
                manifest_valid=pair.manifest_valid,
                split_disjoint=pair.split_disjoint,
                fingerprint_valid=pair.fingerprint_valid,
                threshold=float(manifest.routing_accuracy_target),
                passed=passed,
                execution_count=execution_count,
            )
        except Exception:
            raise V4RunnerError("evaluation_failed") from None

    def _write_report(self, report: V4SafeAggregate) -> None:
        filenames = {
            "manifest": "manifest-latest.json",
            "calibration": "calibration-latest.json",
            "heldout": "heldout-latest.json",
        }
        filename = filenames[report.evaluation_kind]
        root = self._report_root
        if root.is_symlink() or root.parent.is_symlink():
            raise V4RunnerError("report_write_failed")
        temporary: str | None = None
        try:
            root.mkdir(parents=True, exist_ok=True)
            if root.resolve().is_symlink():
                raise OSError
            descriptor, temporary = tempfile.mkstemp(prefix=".v4-report-", suffix=".tmp", dir=root)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(report.to_json())
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / filename)
            temporary = None
            if os.name != "nt":
                directory = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception:
            raise V4RunnerError("report_write_failed") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _sealed(self, operation: Callable[[], T]) -> T:
        contained = contain_evaluation_output(operation)
        if not contained.ok:
            if contained.error is not None:
                raise contained.error
            raise V4RunnerError("evaluation_failed") from None
        return cast(T, contained.value)

    @staticmethod
    def _paired_strata_counts(pair: _ManifestPair) -> tuple[tuple[str, int], ...]:
        counts: list[tuple[str, int]] = []
        for label, manifest in (("calibration", pair.calibration), ("heldout", pair.heldout)):
            counts.extend(
                (f"{label}/{key}", count) for key, count in manifest.routing_stratum_counts.items()
            )
        return tuple(counts)

    @staticmethod
    def _stratum_accuracies(
        totals: tuple[tuple[str, int], ...], correct: tuple[tuple[str, int], ...]
    ) -> tuple[tuple[str, float | None], ...]:
        correct_by_key = dict(correct)
        return tuple(
            (key, (correct_by_key[key] / total) if total else None) for key, total in totals
        )

    @staticmethod
    def _terminal_kind_errors(
        manifest: V4EvaluationManifest, traces: tuple[V4EvaluationTrace, ...]
    ) -> int:
        by_id = {trace.case_id: trace for trace in traces}
        return sum(
            trace.observed_action is case.expected_action
            and trace.observed_terminal_kind is not case.expected_terminal_kind
            for case in manifest.routing_cases
            if case.expected_terminal_kind is not None
            for trace in (by_id[case.case_id],)
        )


def _strict_list_of_dicts(value: object, keys: set[str]) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) or set(item) != keys for item in value
    ):
        raise ValueError("aggregate schema does not match")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("aggregate schema does not match")
    return value


def _ratio(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("aggregate schema does not match")
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError("aggregate schema does not match")
    return float(value)
