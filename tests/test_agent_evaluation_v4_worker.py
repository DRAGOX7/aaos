from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import aaos.agent.evaluation.v4_worker as v4_worker
from aaos.agent.evaluation.v4_runner import V4RunnerError, V4SafeAggregate

SECRET = "SECRET_V4_CASE_DO_NOT_LEAK"


def _synthetic_manifest_aggregate() -> V4SafeAggregate:
    return V4SafeAggregate(
        benchmark_version="synthetic-v1",
        evaluation_kind="manifest",
        candidate_sha="a" * 40,
        case_count=0,
        routing_case_count=0,
        trusted_system_case_count=0,
        trusted_system_pass_count=None,
        security_hard_gate_failure_count=None,
        runtime_failure_count=None,
        model_unavailable_count=None,
        model_timeout_count=None,
        strata_counts=(),
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
        manifest_valid=True,
        split_disjoint=True,
        fingerprint_valid=True,
        threshold=0.5,
        passed=True,
        execution_count=0,
    )


@pytest.mark.parametrize(
    ("runner_error", "expected_exit_code"),
    [("manifest_failed", 10), ("report_write_failed", 11)],
)
def test_manifest_worker_maps_runner_failures_to_closed_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runner_error: str,
    expected_exit_code: int,
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def verify_manifest(self) -> None:
            raise V4RunnerError(runner_error)

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == expected_exit_code
    assert captured.out == ""
    assert captured.err == ""
    assert not response_path.exists()
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


def test_manifest_worker_does_not_print_or_propagate_synthetic_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def verify_manifest(self) -> None:
            raise RuntimeError(SECRET)

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == ""
    assert SECRET not in captured.out + captured.err
    assert not response_path.exists()
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


@pytest.mark.parametrize(
    ("runner_error", "expected_exit_code"),
    [
        ("manifest_failed", 10),
        ("report_write_failed", 11),
        ("evaluation_failed", 1),
    ],
)
def test_runner_error_codes_map_to_closed_worker_exit_codes(
    runner_error: str,
    expected_exit_code: int,
) -> None:
    assert v4_worker._runner_error_exit_code(V4RunnerError(runner_error)) == expected_exit_code


def test_manifest_worker_avoids_model_preflight_and_ledger_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    model_calls: list[str] = []
    ledger_calls: list[str] = []
    ledger_paths: list[Path] = []

    def unexpected_model_setup() -> object:
        model_calls.append("executor")
        raise AssertionError("manifest mode must not initialize a model")

    def unexpected_preflight() -> None:
        model_calls.append("preflight")
        raise AssertionError("manifest mode must not run model preflight")

    monkeypatch.setattr(v4_worker, "create_configured_local_executor", unexpected_model_setup)
    monkeypatch.setattr(v4_worker, "check_configured_local_model", unexpected_preflight)

    class _LedgerSpy:
        def __init__(self, path: Path) -> None:
            self.path = path
            ledger_paths.append(path)

        def status(self) -> None:
            ledger_calls.append("status")

        def _reserve_after_preflight(self, *args: object) -> None:
            ledger_calls.append("reserve")

        def consume(self, *args: object) -> None:
            ledger_calls.append("consume")

        def finish(self, *args: object, **kwargs: object) -> None:
            ledger_calls.append("finish")

    monkeypatch.setattr(v4_worker, "V4HeldoutLedger", _LedgerSpy)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            assert isinstance(kwargs["executor"], v4_worker._ManifestOnlyExecutor)
            self.report_root = kwargs["report_root"]

        def verify_manifest(self) -> V4SafeAggregate:
            return _synthetic_manifest_aggregate()

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out == ""
    assert captured.err == ""
    assert model_calls == []
    assert ledger_calls == []
    assert ledger_paths == [local_appdata / "AAOS" / "evaluation" / "v4" / "heldout-ledger.json"]
    assert response_path.exists()
    assert not ledger_paths[0].exists()
    safe_aggregate = json.loads(response_path.read_text(encoding="utf-8"))
    assert safe_aggregate["evaluation_kind"] == "manifest"
    assert safe_aggregate["execution_count"] == 0
    assert safe_aggregate["model_call_count"] == 0
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


def test_worker_ipc_write_failure_uses_closed_protocol_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def verify_manifest(self) -> V4SafeAggregate:
            return _synthetic_manifest_aggregate()

    def fail_atomic_replace(*args: object, **kwargs: object) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)
    monkeypatch.setattr(os, "replace", fail_atomic_replace)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == 12
    assert captured.out == ""
    assert captured.err == ""
    assert SECRET not in captured.out + captured.err
    assert not response_path.exists()
    assert list(response_dir.glob(".v4-ipc-*.tmp")) == []
