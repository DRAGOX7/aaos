"""C10/V4 routing-policy and benchmark-contract tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aaos.agent.evaluation import (
    V4_BENCHMARK_SCHEMA_VERSION,
    V4_MIN_CASES_PER_ROUTING_STRATUM,
    V4_MIN_ROUTING_CASES,
    V4_ROUTING_ACCURACY_TARGET,
    V3EvaluationOwner,
    V4EvaluationManifest,
    V4EvaluationTrace,
    build_v3_manifests,
    build_v4_manifests,
    score_v4_evaluation,
)
from aaos.agent.evaluation.v2 import V2ExpectedAction


def _routing_manifest() -> tuple[V4EvaluationManifest, V4EvaluationManifest]:
    return build_v4_manifests()


def _routing_traces(
    manifest: V4EvaluationManifest, wrong: int = 0
) -> tuple[V4EvaluationTrace, ...]:
    traces: list[V4EvaluationTrace] = []
    for index, case in enumerate(manifest.routing_cases):
        action = case.expected_action
        if index < wrong:
            action = (
                V2ExpectedAction.TERMINAL_TEXT
                if action is V2ExpectedAction.RETRIEVE
                else V2ExpectedAction.RETRIEVE
            )
        traces.append(
            V4EvaluationTrace(
                case_id=case.case_id,
                observed_action=action,
                observed_terminal_kind=case.expected_terminal_kind,
            )
        )
    return tuple(traces)


def _routing_only_manifest(manifest: V4EvaluationManifest) -> V4EvaluationManifest:
    return replace(manifest, cases=manifest.routing_cases)


def test_v4_policy_constants_are_frozen() -> None:
    assert V4_BENCHMARK_SCHEMA_VERSION == "phase5-agent-benchmark-v4"
    assert V4_MIN_ROUTING_CASES == 64
    assert V4_MIN_CASES_PER_ROUTING_STRATUM == 16
    assert V4_ROUTING_ACCURACY_TARGET == 0.95


def test_v4_routing_strata_are_exactly_balanced() -> None:
    calibration, heldout = _routing_manifest()
    expected = {
        "en/retrieve": 16,
        "en/terminal_text": 16,
        "ar/retrieve": 16,
        "ar/terminal_text": 16,
    }
    assert calibration.routing_stratum_counts == expected
    assert heldout.routing_stratum_counts == expected


def test_v4_policy_parameters_cannot_be_lowered() -> None:
    calibration, _ = _routing_manifest()
    with pytest.raises(ValueError, match="routing minimum is immutable"):
        replace(calibration, routing_minimum=63)
    with pytest.raises(ValueError, match="stratum minimum is immutable"):
        replace(calibration, stratum_minimum=15)


def test_v4_source_and_authoring_versions_cannot_change() -> None:
    calibration, _ = _routing_manifest()
    with pytest.raises(ValueError, match="source contract version is immutable"):
        replace(calibration, source_contract_version="phase5-agent-benchmark-v2")
    with pytest.raises(ValueError, match="authoring specification is immutable"):
        replace(calibration, authoring_spec_version="outcome-fitted")


@pytest.mark.parametrize("split_index", [0, 1])
def test_routing_denominator_below_64_is_acceptance_ineligible(split_index: int) -> None:
    calibration, heldout = _routing_manifest()
    manifest = _routing_only_manifest((calibration, heldout)[split_index])
    short_manifest = replace(manifest, cases=manifest.cases[:-1])
    metrics = score_v4_evaluation(short_manifest, _routing_traces(short_manifest))
    assert metrics.routing_acceptance_eligible is False


@pytest.mark.parametrize(("wrong", "expected"), [(3, True), (4, False)])
def test_n64_accuracy_gate_uses_exact_integer_boundary(wrong: int, expected: bool) -> None:
    calibration, _ = _routing_manifest()
    manifest = _routing_only_manifest(calibration)
    metrics = score_v4_evaluation(manifest, _routing_traces(manifest, wrong=wrong))
    assert metrics.model_routing.total == 64
    assert metrics.model_routing.correct == 64 - wrong
    assert metrics.model_routing.minimum_passing_numerator == 61
    assert metrics.routing_acceptance_eligible is expected


def test_missing_routing_stratum_coverage_is_structurally_invalid() -> None:
    calibration, _ = _routing_manifest()
    retrieve_cases = tuple(
        case
        for case in calibration.routing_cases
        if case.expected_action is V2ExpectedAction.RETRIEVE
    )
    manifest = _routing_only_manifest(calibration)
    no_terminal = replace(manifest, cases=retrieve_cases)
    assert no_terminal.routing_coverage_valid is False


def test_subgroup_accuracy_is_diagnostic_not_an_independent_gate() -> None:
    calibration, _ = _routing_manifest()
    manifest = _routing_only_manifest(calibration)
    metrics = score_v4_evaluation(manifest, _routing_traces(manifest, wrong=3))
    assert metrics.model_routing.subgroup_accuracy_hard_gate is False
    assert metrics.routing_acceptance_eligible is True


def test_security_failure_cannot_be_compensated_by_routing_accuracy() -> None:
    calibration, _ = _routing_manifest()
    traces = list(_routing_traces(calibration))
    trusted = next(
        case
        for case in calibration.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    traces.append(
        V4EvaluationTrace(
            case_id=trusted.case_id,
            trusted_condition_instantiated=True,
            trusted_owner_exercised=True,
            trusted_condition_passed=True,
            security_hard_gate_failure_count=1,
        )
    )
    traces.extend(
        V4EvaluationTrace(
            case_id=case.case_id,
            trusted_condition_instantiated=True,
            trusted_owner_exercised=True,
            trusted_condition_passed=True,
        )
        for case in calibration.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
        and case.case_id != trusted.case_id
    )
    metrics = score_v4_evaluation(calibration, traces)
    assert metrics.model_routing.accuracy.ratio == 1.0
    assert metrics.security_hard_gates.failure_count == 1
    assert metrics.formal_acceptance_eligible is False


def test_formal_acceptance_requires_a_nonempty_trusted_domain() -> None:
    calibration, _ = _routing_manifest()
    manifest = _routing_only_manifest(calibration)
    metrics = score_v4_evaluation(manifest, _routing_traces(manifest))
    assert metrics.routing_acceptance_eligible is True
    assert metrics.trusted_system.total == 0
    assert metrics.formal_acceptance_eligible is False


def test_trusted_system_failure_cannot_be_compensated_by_routing_accuracy() -> None:
    calibration, _ = _routing_manifest()
    traces = list(_routing_traces(calibration))
    traces.extend(
        V4EvaluationTrace(
            case_id=case.case_id,
            trusted_condition_instantiated=True,
            trusted_owner_exercised=True,
            trusted_condition_passed=False,
        )
        for case in calibration.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    metrics = score_v4_evaluation(calibration, traces)
    assert metrics.model_routing.accuracy.ratio == 1.0
    assert metrics.trusted_system.correct < metrics.trusted_system.total
    assert metrics.formal_acceptance_eligible is False


def test_v4_split_membership_and_ownership_are_deterministic_and_disjoint() -> None:
    calibration_a, heldout_a = _routing_manifest()
    calibration_b, heldout_b = _routing_manifest()
    assert calibration_a.dataset_fingerprint == calibration_b.dataset_fingerprint
    assert heldout_a.dataset_fingerprint == heldout_b.dataset_fingerprint
    assert set(calibration_a.case_ids).isdisjoint(heldout_a.case_ids)
    assert calibration_a.routing_content_duplicate_count(heldout_a) == 0
    assert all(case.primary_owner_count == 1 for case in calibration_a.cases + heldout_a.cases)


def test_v4_manifest_contains_heldout_before_any_execution() -> None:
    calibration, heldout = _routing_manifest()
    assert calibration.routing_case_count == 64
    assert heldout.routing_case_count == 64
    assert heldout.final_heldout_case_count == len(heldout.cases)


def test_v4_reports_exact_counts_without_blended_accuracy() -> None:
    calibration, _ = _routing_manifest()
    manifest = _routing_only_manifest(calibration)
    metrics = score_v4_evaluation(manifest, _routing_traces(manifest, wrong=3))
    serialized = metrics.to_dict()
    routing_serialized = serialized["model_routing_metrics"]
    assert isinstance(routing_serialized, dict)
    assert routing_serialized["total"] == 64
    assert routing_serialized["correct"] == 61
    assert "agent_accuracy" not in serialized
    assert "blended_agent_accuracy" not in serialized


def test_v4_metrics_reject_forged_stratum_partition() -> None:
    calibration, _ = _routing_manifest()
    manifest = _routing_only_manifest(calibration)
    metrics = score_v4_evaluation(manifest, _routing_traces(manifest))
    with pytest.raises(ValueError, match="stratum totals must partition routing total"):
        replace(
            metrics.model_routing,
            stratum_totals=(
                ("en/retrieve", 15),
                ("en/terminal_text", 16),
                ("ar/retrieve", 16),
                ("ar/terminal_text", 16),
            ),
        )


def test_v1_v2_v3_artifacts_remain_unchanged_when_building_v4() -> None:
    before = build_v3_manifests()
    build_v4_manifests()
    after = build_v3_manifests()
    assert before[0].dataset_fingerprint == after[0].dataset_fingerprint
    assert before[1].dataset_fingerprint == after[1].dataset_fingerprint
