from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from phase6a_evaluation.harness import (
    CaseResult,
    FixtureExecution,
    Phase6AHarness,
    TrustedCoreFixture,
    artifact_tree_contains_case_ids,
    run_calibration_and_retain_synthetic_artifacts,
    run_manifest,
    write_private_bundle,
    write_sanitized_report,
)
from phase6a_evaluation.manifest import (
    build_calibration_cases,
    build_heldout_cases,
    category_counts,
    freeze_manifest,
    language_fixture_ids,
)

_ORIGINAL_IMPORT = __import__


def assert_bundle_has_no_credentials_or_raw_secrets(output_dir: Path) -> None:
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "password" not in text.lower()
        assert "authorization" not in text.lower()
        assert "api_key" not in text.lower()
        json.loads(text)


def test_calibration_and_heldout_manifests_are_disjoint_and_fingerprinted() -> None:
    calibration = freeze_manifest(build_calibration_cases(), version="phase6a-trusted-core-v1")
    heldout = freeze_manifest(build_heldout_cases(), version="phase6a-trusted-core-v1")
    assert len(calibration.cases) == 48
    assert len(heldout.cases) == 24
    assert {case.case_id for case in calibration.cases}.isdisjoint(
        case.case_id for case in heldout.cases
    )
    assert calibration.manifest_sha256 != heldout.manifest_sha256


def test_calibration_case_ids_and_category_matrix_are_frozen() -> None:
    cases = build_calibration_cases()
    assert tuple(case.case_id for case in cases) == tuple(
        f"cal-{index:03d}" for index in range(1, 49)
    )
    assert category_counts(cases) == {
        "approval_expiry": 8,
        "capability_policy": 8,
        "claim_concurrency": 8,
        "dispatch_precondition": 8,
        "outcome_reconciliation": 8,
        "security_import": 8,
    }
    assert language_fixture_ids(cases) == {
        "en": ("cal-041", "cal-042", "cal-043", "cal-044"),
        "ar": ("cal-045", "cal-046", "cal-047", "cal-048"),
    }


def test_heldout_case_ids_and_category_matrix_are_frozen_without_execution() -> None:
    cases = build_heldout_cases()
    assert tuple(case.case_id for case in cases) == tuple(
        f"hold-{index:03d}" for index in range(1, 25)
    )
    assert category_counts(cases) == {
        "approval_expiry": 4,
        "capability_policy": 4,
        "claim_concurrency": 4,
        "dispatch_precondition": 4,
        "outcome_reconciliation": 4,
        "security_import": 4,
    }
    assert language_fixture_ids(cases) == {
        "en": ("hold-021", "hold-022"),
        "ar": ("hold-023", "hold-024"),
    }


def test_ordinary_pytest_path_cannot_consume_heldout() -> None:
    result = run_manifest(build_calibration_cases())
    assert result.heldout_execution_count == 0


def test_harness_makes_no_model_or_rag_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.__import__", _forbid_model_and_rag_import)
    result = run_manifest(build_calibration_cases())
    assert result.model_call_count == 0
    assert result.rag_execution_count == 0


def test_private_bundle_is_sufficient_and_contains_no_credentials(tmp_path: Path) -> None:
    report_hash, bundle_hash = run_calibration_and_retain_synthetic_artifacts(tmp_path)
    assert report_hash and bundle_hash
    assert not artifact_tree_contains_case_ids(tmp_path, prefix="hold-")
    assert_bundle_has_no_credentials_or_raw_secrets(tmp_path)


def test_normal_pytest_path_cannot_reach_real_heldout_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_real_executor(*_: object, **__: object) -> None:
        pytest.fail("real hold-* executor is CLI-only")

    monkeypatch.setattr(
        "phase6a_evaluation.harness.RealHeldoutExecutor.execute",
        forbidden_real_executor,
    )
    result = run_manifest(build_calibration_cases())
    assert result.heldout_execution_count == 0


def test_freeze_manifest_rejects_relabelled_calibration_case() -> None:
    cases = list(build_calibration_cases())
    cases[0] = replace(cases[0], owner="capability")
    with pytest.raises(ValueError, match="canonical"):
        freeze_manifest(cases, version="phase6a-trusted-core-v1")


def test_freeze_manifest_rejects_relabelled_heldout_case() -> None:
    cases = list(build_heldout_cases())
    cases[-1] = replace(cases[-1], setup=cases[-1].setup.ADVERSARIAL_TEXT)
    with pytest.raises(ValueError, match="canonical"):
        freeze_manifest(cases, version="phase6a-trusted-core-v1")


def test_run_manifest_rejects_relabelled_calibration_case() -> None:
    cases = list(build_calibration_cases())
    cases[0] = replace(cases[0], expected_invariant=cases[0].expected_invariant.CAPABILITY_REVALIDATED)
    with pytest.raises(ValueError, match="canonical"):
        run_manifest(cases)


def test_artifact_writers_reject_open_ended_or_sensitive_metrics(tmp_path: Path) -> None:
    case = build_calibration_cases()[0]
    result = CaseResult(
        case.case_id,
        case.owner,
        case.category,
        case.setup,
        case.expected_invariant,
        True,
        cast(Mapping[str, bool | int | float], {"raw_secret": "do-not-persist"}),
    )
    with pytest.raises((TypeError, ValueError), match="metric"):
        write_sanitized_report((result,), tmp_path / "report.json")
    with pytest.raises((TypeError, ValueError), match="metric"):
        write_private_bundle((result,), tmp_path / "bundle.json")


def test_trusted_fixture_result_is_failure_sensitive() -> None:
    class BrokenFixture(TrustedCoreFixture):
        def evaluate(self, case: object) -> FixtureExecution:
            del case
            return self.failed_execution()

    case = build_calibration_cases()[0]
    result = Phase6AHarness(fixture=BrokenFixture()).execute_case(case)
    assert result.passed is False


def _forbid_model_and_rag_import(
    name: str,
    globals: Mapping[str, object] | None = None,
    locals: Mapping[str, object] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> object:
    if "model" in name.lower() or "rag" in name.lower():
        raise AssertionError(f"forbidden model/RAG import: {name}")
    return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
