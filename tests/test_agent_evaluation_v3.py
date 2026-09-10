"""C9 ownership-aware Phase 5 evaluation contract tests."""

from __future__ import annotations

from dataclasses import replace
from typing import MutableMapping, cast

import pytest

from aaos.agent.evaluation.contracts import BenchmarkCategory
from aaos.agent.evaluation.v2 import V2ExpectedAction, build_v2_manifests
from aaos.agent.evaluation.v3 import (
    V2_ARCHITECTURALLY_MIXED_HISTORICAL_METRIC,
    V2_HISTORICAL_METRIC_LABEL,
    V3_BENCHMARK_SCHEMA_VERSION,
    V3_CATEGORY_OWNER_MAP,
    V3_METRIC_SCHEMA_VERSION,
    V3_SCHEMA_FINGERPRINT,
    V3EvaluationManifest,
    V3EvaluationOwner,
    V3EvaluationTrace,
    build_v3_manifests,
    migrate_v2_cases,
    owner_for_category,
    score_v3_evaluation,
)


def _all_pass_traces(manifest: V3EvaluationManifest) -> tuple[V3EvaluationTrace, ...]:
    cases = manifest.cases
    return tuple(
        V3EvaluationTrace(
            case_id=case.case_id,
            observed_action=case.expected_action,
            observed_terminal_kind=case.expected_terminal_kind,
            trusted_condition_passed=True,
        )
        for case in cases
    )


def test_v3_manifests_are_new_contracts_with_frozen_membership() -> None:
    calibration, heldout = build_v3_manifests()

    assert calibration.schema_version == V3_BENCHMARK_SCHEMA_VERSION
    assert heldout.schema_version == V3_BENCHMARK_SCHEMA_VERSION
    assert calibration.calibration_case_count == 72
    assert calibration.language_counts("calibration") == {"en": 36, "ar": 36}
    assert heldout.final_heldout_case_count == 24
    assert heldout.language_counts("final_heldout") == {"en": 12, "ar": 12}
    assert calibration.schema_fingerprint == V3_SCHEMA_FINGERPRINT
    assert calibration.schema_fingerprint != ""
    assert calibration.to_dict()["source_contract_version"] == "phase5-agent-benchmark-v2"

    v2_calibration, v2_heldout = build_v2_manifests()
    assert calibration.case_ids == v2_calibration.case_ids
    assert heldout.case_ids == v2_heldout.case_ids
    assert calibration.dataset_fingerprint != v2_calibration.dataset_fingerprint


def test_v3_ownership_mapping_is_complete_deterministic_and_architecture_derived() -> None:
    first = tuple((category, owner_for_category(category)) for category in BenchmarkCategory)
    second = tuple((category, owner_for_category(category)) for category in BenchmarkCategory)

    assert first == second
    assert len(first) == len(tuple(BenchmarkCategory))
    assert all(isinstance(owner, V3EvaluationOwner) for _, owner in first)
    with pytest.raises(TypeError):
        cast(MutableMapping[BenchmarkCategory, V3EvaluationOwner], V3_CATEGORY_OWNER_MAP)[
            BenchmarkCategory.RAG_QUERY
        ] = V3EvaluationOwner.TRUSTED_POST_TOOL_RESULT


def test_every_v3_case_has_exactly_one_primary_owner() -> None:
    calibration, heldout = build_v3_manifests()
    for case in calibration.cases + heldout.cases:
        assert isinstance(case.owner, V3EvaluationOwner)
        assert case.primary_owner_count == 1


def test_v3_case_cannot_override_the_architecture_owner_mapping() -> None:
    calibration, _ = build_v3_manifests()
    case = next(
        case for case in calibration.cases if case.owner is V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    with pytest.raises(ValueError, match="architecture-derived"):
        type(case)(
            case_id=case.case_id,
            split=case.split,
            category=case.category,
            language=case.language,
            request_text=case.request_text,
            owner=V3EvaluationOwner.TRUSTED_POST_TOOL_RESULT,
            expected_action=None,
        )


def test_v3_ownership_counts_match_frozen_calibration_shape() -> None:
    calibration, _ = build_v3_manifests()
    counts = calibration.ownership_counts()

    assert counts[V3EvaluationOwner.MODEL_SEMANTIC_ROUTING] == 18
    assert counts[V3EvaluationOwner.TRUSTED_PRE_MODEL_CONTROL] == 4
    assert counts[V3EvaluationOwner.TRUSTED_POST_MODEL_VALIDATION] == 1
    assert counts[V3EvaluationOwner.TRUSTED_RUNTIME_OR_LIFECYCLE] == 14
    assert counts[V3EvaluationOwner.TRUSTED_POST_TOOL_RESULT] == 5
    assert counts[V3EvaluationOwner.EVALUATION_SYNTHETIC_FAULT_INJECTION] == 30
    assert sum(counts.values()) == 72


def test_v3_score_domains_are_separate_and_no_blended_accuracy_is_emitted() -> None:
    calibration, _ = build_v3_manifests()
    metrics = score_v3_evaluation(calibration, _all_pass_traces(calibration))

    assert metrics.model_routing.total == 18
    assert metrics.model_routing.correct == 18
    assert metrics.model_routing.accuracy.ratio == 1.0
    assert metrics.trusted_system.total == 54
    assert metrics.trusted_system.correct == 54
    assert metrics.proposal_format_efficiency.total == 18
    assert metrics.security_hard_gates.failure_count == 0
    serialized = metrics.to_dict()
    assert set(serialized) >= {
        "model_routing_metrics",
        "trusted_system_metrics",
        "security_hard_gates",
        "runtime_environment_metrics",
        "proposal_format_efficiency_metrics",
    }
    assert serialized["MODEL_ROUTING_STATUS"] == "PASS"
    assert serialized["TRUSTED_SYSTEM_STATUS"] == "PASS"
    assert serialized["SECURITY_HARD_GATE_STATUS"] == "PASS"
    assert serialized[V2_HISTORICAL_METRIC_LABEL] == V2_ARCHITECTURALLY_MIXED_HISTORICAL_METRIC
    assert "agent_accuracy" not in serialized


def test_routing_error_is_not_hidden_by_trusted_system_success() -> None:
    calibration, _ = build_v3_manifests()
    model_case = next(
        case for case in calibration.cases if case.owner is V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    wrong_action = (
        V2ExpectedAction.TERMINAL_TEXT
        if model_case.expected_action is V2ExpectedAction.RETRIEVE
        else V2ExpectedAction.RETRIEVE
    )
    traces = list(_all_pass_traces(calibration))
    index = next(index for index, trace in enumerate(traces) if trace.case_id == model_case.case_id)
    traces[index] = replace(traces[index], observed_action=wrong_action)

    metrics = score_v3_evaluation(calibration, traces)

    assert metrics.model_routing.correct == 17
    assert metrics.trusted_system.correct == 54
    assert metrics.security_hard_gates.failure_count == 0


def test_trusted_system_failure_is_not_hidden_by_perfect_routing() -> None:
    calibration, _ = build_v3_manifests()
    trusted_case = next(
        case
        for case in calibration.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    traces = list(_all_pass_traces(calibration))
    index = next(
        index for index, trace in enumerate(traces) if trace.case_id == trusted_case.case_id
    )
    traces[index] = replace(traces[index], trusted_condition_passed=False)

    metrics = score_v3_evaluation(calibration, traces)

    assert metrics.model_routing.correct == 18
    assert metrics.trusted_system.correct == 53
    assert metrics.trusted_system.status == "FAIL"


def test_security_hard_gate_is_independent_zero_tolerance_domain() -> None:
    calibration, _ = build_v3_manifests()
    traces = list(_all_pass_traces(calibration))
    traces[0] = replace(traces[0], security_hard_gate_failure_count=1)

    metrics = score_v3_evaluation(calibration, traces)

    assert metrics.model_routing.correct == 18
    assert metrics.trusted_system.correct == 54
    assert metrics.security_hard_gates.failure_count == 1
    assert metrics.security_hard_gates.status == "FAIL"


def test_v3_mapping_and_migration_do_not_accept_model_results() -> None:
    v2_calibration, _ = build_v2_manifests()
    first = migrate_v2_cases(v2_calibration.cases)
    second = migrate_v2_cases(v2_calibration.cases)

    assert first == second
    assert all(case.owner is owner_for_category(case.category) for case in first)

    with pytest.raises(TypeError):
        owner_for_category("rag_query")  # type: ignore[arg-type]


def test_v3_trace_schema_is_versioned_and_stateless() -> None:
    trace = V3EvaluationTrace(case_id="cal-001", trusted_condition_passed=True)
    assert cast(str, trace.to_dict()["trace_schema_version"]).startswith("phase5-agent-trace-v3")
    assert V3_METRIC_SCHEMA_VERSION.startswith("phase5-agent-evaluation-v3")
    assert "request_text" not in trace.to_dict()


def test_v3_routing_scorer_cross_check_shape_without_model_calls() -> None:
    v3_calibration, _ = build_v3_manifests()
    model_cases = [
        case
        for case in v3_calibration.cases
        if case.owner is V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    ]
    traces = []
    for case in v3_calibration.cases:
        traces.append(
            V3EvaluationTrace(
                case_id=case.case_id,
                observed_action=case.expected_action,
                observed_terminal_kind=case.expected_terminal_kind,
                trusted_condition_passed=True,
            )
        )
    wrong_case = model_cases[-1]
    wrong_index = next(
        index for index, trace in enumerate(traces) if trace.case_id == wrong_case.case_id
    )
    traces[wrong_index] = replace(
        traces[wrong_index],
        observed_action=(
            V2ExpectedAction.TERMINAL_TEXT
            if wrong_case.expected_action is V2ExpectedAction.RETRIEVE
            else V2ExpectedAction.RETRIEVE
        ),
    )
    metrics = score_v3_evaluation(v3_calibration, traces)
    assert metrics.model_routing.total == 18
    assert metrics.model_routing.correct == 17
