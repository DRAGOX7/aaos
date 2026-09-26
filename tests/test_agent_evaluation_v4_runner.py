from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import pytest

from aaos.agent.contracts import TerminalResultKind
from aaos.agent.evaluation.contracts import (
    BenchmarkCategory,
    EvaluationLanguage,
    EvaluationSplit,
)
from aaos.agent.evaluation.v2 import V2ExpectedAction
from aaos.agent.evaluation.v3 import V3BenchmarkCase, V3EvaluationOwner
from aaos.agent.evaluation.v4 import V4EvaluationManifest, V4EvaluationTrace, score_v4_evaluation
from aaos.agent.evaluation.v4_ledger import (
    HeldoutLedgerError,
    HeldoutState,
    V4HeldoutLedger,
)
from aaos.agent.evaluation.v4_runner import (
    V4SafeAggregate,
    V4SealedRunner,
    contain_evaluation_output,
)

SECRET = "SECRET_V4_CASE_DO_NOT_LEAK"
CANDIDATE_SHA = "a" * 40


def _manifest(split: EvaluationSplit, prefix: str) -> V4EvaluationManifest:
    cases: list[V3BenchmarkCase] = []
    for language in EvaluationLanguage:
        for action in V2ExpectedAction:
            category = (
                BenchmarkCategory.RAG_QUERY
                if action is V2ExpectedAction.RETRIEVE
                else BenchmarkCategory.CONTROL_TERMINAL
            )
            for index in range(16):
                case_id = f"{prefix}-{language.value}-{action.value}-{index:02d}"
                cases.append(
                    V3BenchmarkCase(
                        case_id=case_id,
                        split=split,
                        category=category,
                        language=language,
                        request_text=f"{SECRET} synthetic {prefix} {case_id}",
                        owner=V3EvaluationOwner.MODEL_SEMANTIC_ROUTING,
                        expected_action=action,
                        expected_terminal_kind=(
                            TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL
                            if action is V2ExpectedAction.TERMINAL_TEXT
                            else None
                        ),
                    )
                )
    cases.append(
        V3BenchmarkCase(
            case_id=f"{prefix}-trusted-synthetic",
            split=split,
            category=BenchmarkCategory.UNKNOWN_TOOL,
            language=EvaluationLanguage.ENGLISH,
            request_text=f"{SECRET} synthetic trusted fixture",
            owner=V3EvaluationOwner.EVALUATION_SYNTHETIC_FAULT_INJECTION,
            expected_no_tool=True,
        )
    )
    return V4EvaluationManifest(
        benchmark_version="phase5-agent-benchmark-v4",
        cases=tuple(cases),
        split=split,
    )


def _traces(manifest: V4EvaluationManifest) -> tuple[V4EvaluationTrace, ...]:
    traces: list[V4EvaluationTrace] = []
    for case in manifest.cases:
        if case.owner is V3EvaluationOwner.MODEL_SEMANTIC_ROUTING:
            traces.append(
                V4EvaluationTrace(
                    case_id=case.case_id,
                    observed_action=case.expected_action,
                    observed_terminal_kind=case.expected_terminal_kind,
                    proposal_format_valid=True,
                    model_calls=1,
                )
            )
        else:
            traces.append(
                V4EvaluationTrace(
                    case_id=case.case_id,
                    trusted_condition_instantiated=True,
                    trusted_owner_exercised=True,
                    trusted_condition_passed=True,
                )
            )
    return tuple(traces)


def _runner(
    root: Path,
    *,
    preflight: Callable[[], None] = lambda: None,
    executor: Callable[[V4EvaluationManifest], tuple[V4EvaluationTrace, ...]] | None = None,
    fake_only: bool = True,
) -> V4SealedRunner:
    calibration = _manifest(EvaluationSplit.CALIBRATION, "cal")
    heldout = _manifest(EvaluationSplit.FINAL_HELDOUT, "hold")
    return V4SealedRunner(
        manifest_factory=lambda: (calibration, heldout),
        scorer=score_v4_evaluation,
        executor=executor or _traces,
        candidate_sha=CANDIDATE_SHA,
        ledger=V4HeldoutLedger(root / "heldout-ledger.json"),
        report_root=root / "reports",
        preflight=preflight,
        fake_only=fake_only,
    )


def test_output_containment_discards_stdout_and_stderr_semantics() -> None:
    def noisy() -> None:
        print(SECRET)
        import sys

        print(SECRET, file=sys.stderr)

    result = contain_evaluation_output(noisy)

    assert result.ok is True
    assert SECRET not in result.public_text
    assert len(result.public_text) < 512


def test_output_containment_sanitizes_exception_semantics() -> None:
    def fail() -> None:
        raise RuntimeError(SECRET)

    result = contain_evaluation_output(fail)

    assert result.ok is False
    assert SECRET not in result.public_text
    assert "RuntimeError" not in result.public_text


def test_aggregate_schema_is_closed_and_never_serializes_case_content() -> None:
    report = V4SafeAggregate(
        benchmark_version="phase5-agent-benchmark-v4",
        evaluation_kind="calibration",
        candidate_sha=CANDIDATE_SHA,
        case_count=65,
        routing_case_count=64,
        trusted_system_case_count=1,
        trusted_system_pass_count=1,
        security_hard_gate_failure_count=0,
        runtime_failure_count=0,
        model_unavailable_count=0,
        model_timeout_count=0,
        strata_counts=(
            ("en/retrieve", 16),
            ("en/terminal", 16),
            ("ar/retrieve", 16),
            ("ar/terminal", 16),
        ),
        global_routing_accuracy=1.0,
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
        model_call_count=64,
        rag_execution_count=0,
        manifest_valid=True,
        split_disjoint=True,
        fingerprint_valid=True,
        threshold=0.95,
        passed=True,
        execution_count=0,
    )

    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert SECRET not in encoded
    assert "case_id" not in payload
    assert "request_text" not in payload
    assert V4SafeAggregate.from_dict(payload) == report
    with pytest.raises(ValueError, match="aggregate schema"):
        V4SafeAggregate.from_dict({**payload, "request_text": SECRET})
    with pytest.raises(ValueError, match="aggregate schema"):
        V4SafeAggregate.from_dict(
            {
                **payload,
                "strata_counts": [{"count": 64, "request_text": SECRET, "stratum": "en/retrieve"}],
            }
        )
    with pytest.raises(ValueError, match="aggregate schema"):
        V4SafeAggregate.from_dict({**payload, "global_routing_accuracy": True})
    with pytest.raises(ValueError, match="aggregate schema"):
        V4SafeAggregate.from_dict({**payload, "invalid_output_count": 1})


def test_manifest_mode_returns_only_safe_aggregates(tmp_path: Path) -> None:
    report = _runner(tmp_path).verify_manifest()

    assert report.evaluation_kind == "manifest"
    assert report.case_count == 130
    assert report.routing_case_count == 128
    assert report.trusted_system_case_count == 2
    assert report.trusted_system_pass_count is None
    assert report.security_hard_gate_failure_count is None
    assert report.runtime_failure_count is None
    assert report.model_unavailable_count is None
    assert report.model_timeout_count is None
    assert report.manifest_valid is True
    assert report.split_disjoint is True
    assert report.fingerprint_valid is True
    assert SECRET not in json.dumps(report.to_dict(), sort_keys=True)
    assert not (tmp_path / "heldout-ledger.json").exists()


def test_calibration_mode_uses_canonical_scorer_and_safe_aggregate(tmp_path: Path) -> None:
    report = _runner(tmp_path).calibrate()

    assert report.evaluation_kind == "calibration"
    assert report.case_count == 65
    assert report.routing_case_count == 64
    assert report.trusted_system_case_count == 1
    assert report.trusted_system_pass_count == 1
    assert report.security_hard_gate_failure_count == 0
    assert report.runtime_failure_count == 0
    assert report.model_unavailable_count == 0
    assert report.model_timeout_count == 0
    assert report.global_routing_accuracy == 1.0
    assert report.passed is True
    assert report.model_call_count == 64
    assert SECRET not in json.dumps(report.to_dict(), sort_keys=True)
    assert not (tmp_path / "heldout-ledger.json").exists()


def test_available_to_reserved_acquisition_is_durable(tmp_path: Path) -> None:
    ledger = V4HeldoutLedger(tmp_path / "ledger.json")

    reservation = ledger.reserve(CANDIDATE_SHA)

    assert ledger.status().state is HeldoutState.RESERVED
    assert ledger.status().execution_count == 0
    assert reservation.reservation_id


def test_concurrent_reservations_allow_exactly_one_winner(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"

    def acquire() -> bool:
        try:
            V4HeldoutLedger(ledger_path).reserve(CANDIDATE_SHA)
            return True
        except HeldoutLedgerError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: acquire(), range(2)))

    assert sum(outcomes) == 1
    assert V4HeldoutLedger(ledger_path).status().state is HeldoutState.RESERVED


def test_presemantic_preflight_failure_releases_reserved_permit(tmp_path: Path) -> None:
    def fail_preflight() -> None:
        raise RuntimeError(SECRET)

    runner = _runner(tmp_path, preflight=fail_preflight)
    with pytest.raises(Exception):
        runner.evaluate_heldout()

    status = V4HeldoutLedger(tmp_path / "heldout-ledger.json").status()
    assert status.state is HeldoutState.AVAILABLE
    assert status.execution_count == 0
    assert SECRET not in str(status.to_dict())


def test_heldout_becomes_consumed_before_manifest_materialization(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    observed: list[HeldoutState] = []

    def inspect_before_semantic_access(
        manifest: V4EvaluationManifest,
    ) -> tuple[V4EvaluationTrace, ...]:
        observed.append(V4HeldoutLedger(tmp_path / "heldout-ledger.json").status().state)
        return _traces(manifest)

    runner = _runner(tmp_path, executor=inspect_before_semantic_access)
    runner.evaluate_heldout()

    assert observed == [HeldoutState.CONSUMED]


def test_consumed_is_permanent_and_second_reservation_fails(tmp_path: Path) -> None:
    ledger = V4HeldoutLedger(tmp_path / "ledger.json")
    reservation = ledger.reserve(CANDIDATE_SHA)
    ledger.consume(reservation.reservation_id)

    with pytest.raises(HeldoutLedgerError):
        ledger.reserve(CANDIDATE_SHA)
    assert ledger.status().state is HeldoutState.CONSUMED
    assert ledger.status().execution_count == 1


def test_postsemantic_crash_leaves_consumed_state(tmp_path: Path) -> None:
    def crash(_: V4EvaluationManifest) -> tuple[V4EvaluationTrace, ...]:
        raise RuntimeError(SECRET)

    with pytest.raises(Exception):
        _runner(tmp_path, executor=crash).evaluate_heldout()

    status = V4HeldoutLedger(tmp_path / "heldout-ledger.json").status()
    assert status.state is HeldoutState.CONSUMED
    assert status.execution_count == 1
    assert SECRET not in str(status.to_dict())


def test_process_exit_after_executor_start_leaves_consumed_state(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ledger_path = tmp_path / "ledger.json"
    report_root = tmp_path / "reports"
    execution_marker = tmp_path / "synthetic-execution-started.txt"
    child_script = tmp_path / "crash_after_consumption.py"
    child_script.write_text(
        f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(repo_root / "src")!r})
sys.path.insert(0, {str(repo_root / "tests")!r})
from test_agent_evaluation_v4_runner import CANDIDATE_SHA, _manifest, _traces
from aaos.agent.evaluation.contracts import EvaluationSplit
from aaos.agent.evaluation.v4 import score_v4_evaluation
from aaos.agent.evaluation.v4_ledger import V4HeldoutLedger
from aaos.agent.evaluation.v4_runner import V4SealedRunner
calibration = _manifest(EvaluationSplit.CALIBRATION, "child-cal")
heldout = _manifest(EvaluationSplit.FINAL_HELDOUT, "child-heldout")
def terminate_after_synthetic_start(manifest):
    Path({str(execution_marker)!r}).write_text("started", encoding="utf-8")
    os._exit(23)
runner = V4SealedRunner(
    manifest_factory=lambda: (calibration, heldout),
    scorer=score_v4_evaluation,
    executor=terminate_after_synthetic_start,
    candidate_sha=CANDIDATE_SHA,
    ledger=V4HeldoutLedger(Path({str(ledger_path)!r})),
    report_root=Path({str(report_root)!r}),
)
runner.evaluate_heldout()
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(child_script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    status = V4HeldoutLedger(ledger_path).status()
    assert result.returncode == 23
    assert result.stdout == ""
    assert result.stderr == ""
    assert execution_marker.read_text(encoding="utf-8") == "started"
    assert status.state is HeldoutState.CONSUMED
    assert status.execution_count == 1
    assert status.completion_status == "running"


def test_stale_reserved_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = V4HeldoutLedger(path)
    ledger.reserve(CANDIDATE_SHA)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reserved_at_utc"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reopened = V4HeldoutLedger(path)

    with pytest.raises(HeldoutLedgerError):
        reopened.reserve(CANDIDATE_SHA)
    assert reopened.status().state is HeldoutState.RESERVED


def test_corrupt_ledger_fails_closed_without_reset(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not-json", encoding="utf-8")
    ledger = V4HeldoutLedger(path)

    with pytest.raises(HeldoutLedgerError):
        ledger.status()
    with pytest.raises(HeldoutLedgerError):
        ledger.reserve(CANDIDATE_SHA)
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_second_heldout_attempt_is_refused_without_reexecution(tmp_path: Path) -> None:
    executions: list[str] = []

    def execute(manifest: V4EvaluationManifest) -> tuple[V4EvaluationTrace, ...]:
        executions.append(manifest.split.value)
        return _traces(manifest)

    runner = _runner(tmp_path, executor=execute)
    runner.evaluate_heldout()
    with pytest.raises(HeldoutLedgerError):
        runner.evaluate_heldout()

    assert executions == [EvaluationSplit.FINAL_HELDOUT.value]


def test_fake_only_executor_boundary_rejects_live_rag_or_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fake-only"):
        _runner(
            tmp_path,
            executor=lambda _: _traces(_manifest(EvaluationSplit.CALIBRATION, "live")),
            fake_only=False,
        )


def test_report_store_rejects_paths_outside_private_root(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    report = runner.verify_manifest()

    assert report.to_dict()["execution_count"] == 0
    assert not (tmp_path.parent / "manifest-latest.json").exists()


def test_concurrent_process_reservations_allow_exactly_one_winner(tmp_path: Path) -> None:
    import subprocess
    import sys

    ledger_path = tmp_path / "process-ledger.json"
    program = r"""
import sys
from aaos.agent.evaluation.v4_ledger import HeldoutLedgerError, V4HeldoutLedger
try:
    V4HeldoutLedger(sys.argv[1]).reserve(sys.argv[2])
except HeldoutLedgerError:
    print("blocked")
else:
    print("won")
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(ledger_path), CANDIDATE_SHA],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outcomes = [process.communicate(timeout=15) for process in processes]

    assert all(process.returncode == 0 for process in processes)
    assert sorted(stdout.strip() for stdout, _ in outcomes) == ["blocked", "won"]
    assert all(stderr == "" for _, stderr in outcomes)


def test_calibration_preflight_failure_does_not_materialize_manifests(tmp_path: Path) -> None:
    calibration = _manifest(EvaluationSplit.CALIBRATION, "cal-preflight")
    heldout = _manifest(EvaluationSplit.FINAL_HELDOUT, "hold-preflight")
    materialized = 0

    def factory() -> tuple[V4EvaluationManifest, V4EvaluationManifest]:
        nonlocal materialized
        materialized += 1
        return calibration, heldout

    def fail_preflight() -> None:
        raise RuntimeError(SECRET)

    runner = V4SealedRunner(
        manifest_factory=factory,
        scorer=score_v4_evaluation,
        executor=_traces,
        candidate_sha=CANDIDATE_SHA,
        ledger=V4HeldoutLedger(tmp_path / "ledger.json"),
        report_root=tmp_path / "reports",
        preflight=fail_preflight,
    )

    with pytest.raises(Exception):
        runner.calibrate()

    assert materialized == 0
    assert not (tmp_path / "ledger.json").exists()
    assert not (tmp_path / "reports").exists()


def test_heldout_is_consumed_before_manifest_factory_runs(tmp_path: Path) -> None:
    calibration = _manifest(EvaluationSplit.CALIBRATION, "cal-order")
    heldout = _manifest(EvaluationSplit.FINAL_HELDOUT, "hold-order")
    ledger_path = tmp_path / "ledger.json"
    observed_states: list[HeldoutState] = []

    def factory() -> tuple[V4EvaluationManifest, V4EvaluationManifest]:
        observed_states.append(V4HeldoutLedger(ledger_path).status().state)
        return calibration, heldout

    runner = V4SealedRunner(
        manifest_factory=factory,
        scorer=score_v4_evaluation,
        executor=_traces,
        candidate_sha=CANDIDATE_SHA,
        ledger=V4HeldoutLedger(ledger_path),
        report_root=tmp_path / "reports",
    )

    runner.evaluate_heldout()

    assert observed_states == [HeldoutState.CONSUMED]


def test_reading_available_ledger_status_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    ledger_path = tmp_path / "not-created" / "heldout-ledger.json"
    ledger = V4HeldoutLedger(ledger_path)

    status = ledger.status()

    assert status.state is HeldoutState.AVAILABLE
    assert not ledger_path.parent.exists()


def test_presemantic_release_cannot_reverse_consumed_state(tmp_path: Path) -> None:
    from aaos.agent.evaluation.v4_ledger import PreSemanticFailureEvidence

    ledger = V4HeldoutLedger(tmp_path / "ledger.json")
    reservation = ledger.reserve(CANDIDATE_SHA)
    ledger.consume(reservation.reservation_id)
    evidence = PreSemanticFailureEvidence(
        preflight_failed=True,
        protected_case_materialized=False,
        model_or_tool_execution_started=False,
        scoring_started=False,
        case_result_produced=False,
    )

    with pytest.raises(HeldoutLedgerError):
        ledger.release_presemantic(reservation.reservation_id, evidence)
    assert ledger.status().state is HeldoutState.CONSUMED
