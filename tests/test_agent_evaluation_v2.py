"""Versioned C8 evaluation-contract tests (RED before implementation)."""

from __future__ import annotations

from aaos.agent.evaluation.v2 import (
    V2_BENCHMARK_SCHEMA_VERSION,
    V2_EXPECTED_ACTION_RETRIEVE,
    V2_EXPECTED_ACTION_TERMINAL,
    V2_METRIC_SCHEMA_VERSION,
    V2_SCHEMA_FINGERPRINT,
    V2EvaluationTrace,
    build_v2_manifests,
    score_v2_evaluation,
)


def test_v2_is_a_distinct_contract_and_preserves_v1_split() -> None:
    calibration, heldout = build_v2_manifests()
    assert calibration.schema_version == V2_BENCHMARK_SCHEMA_VERSION
    assert heldout.schema_version == V2_BENCHMARK_SCHEMA_VERSION
    assert calibration.calibration_case_count == 72
    assert calibration.language_counts("calibration") == {"en": 36, "ar": 36}
    assert heldout.final_heldout_case_count == 24
    assert heldout.language_counts("final_heldout") == {"en": 12, "ar": 12}
    assert V2_METRIC_SCHEMA_VERSION != "phase5-agent-evaluation-v1"
    assert calibration.schema_fingerprint == V2_SCHEMA_FINGERPRINT
    assert calibration.schema_fingerprint != ""


def test_v2_model_input_excludes_trusted_tool_and_scope_oracles() -> None:
    calibration, _ = build_v2_manifests()
    case = calibration.cases[0]
    visible = case.model_visible_input()
    assert set(visible) == {"request_text"}
    assert case.expected_action in {V2_EXPECTED_ACTION_RETRIEVE, V2_EXPECTED_ACTION_TERMINAL}
    assert "expected_tool_name" not in visible
    assert "scope_oracle" not in visible


def test_v2_scoring_uses_retrieve_or_terminal_semantics() -> None:
    calibration, _ = build_v2_manifests()
    traces = tuple(
        V2EvaluationTrace(case.case_id, observed_action=case.expected_action)
        for case in calibration.cases
    )
    metrics = score_v2_evaluation(calibration, traces)
    assert metrics.action_accuracy.ratio == 1.0
    assert metrics.to_dict()["metric_schema_version"] == V2_METRIC_SCHEMA_VERSION
