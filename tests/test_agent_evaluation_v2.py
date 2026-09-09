"""Versioned C8 evaluation-contract tests (RED before implementation)."""

from __future__ import annotations

from typing import Any, cast

from aaos.agent.contracts import AgentErrorCode
from aaos.agent.evaluation.v2 import (
    V2_BENCHMARK_SCHEMA_VERSION,
    V2_EXPECTED_ACTION_RETRIEVE,
    V2_EXPECTED_ACTION_TERMINAL,
    V2_METRIC_SCHEMA_VERSION,
    V2_SCHEMA_FINGERPRINT,
    V2_TRACE_SCHEMA_VERSION,
    V2EvaluationTrace,
    V2ExpectedAction,
    V2FailureClass,
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
    assert V2_TRACE_SCHEMA_VERSION != "phase5-agent-trace-v1"
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


def _traces_with_override(case_id: str, **kwargs: object) -> tuple[V2EvaluationTrace, ...]:
    calibration, _ = build_v2_manifests()
    traces: list[V2EvaluationTrace] = []
    for case in calibration.cases:
        values: dict[str, object] = {
            "observed_action": case.expected_action,
            "observed_terminal_kind": case.expected_terminal_kind,
        }
        if case.case_id == case_id:
            values.update(kwargs)
        traces.append(V2EvaluationTrace(case.case_id, **cast(Any, values)))
    return tuple(traces)


def test_v2_repairable_format_event_is_not_a_final_invalid_proposal() -> None:
    calibration, _ = build_v2_manifests()
    case = calibration.cases[0]
    metrics = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_action=case.expected_action,
            proposal_format_valid=True,
            proposal_repairs=1,
            proposal_format_event_count=1,
        ),
    )

    assert metrics.invalid_proposal_count == 0
    assert metrics.proposal_format_event_count == 1
    assert metrics.failure_class_counts == ()


def test_v2_failed_repair_is_a_final_invalid_proposal() -> None:
    calibration, _ = build_v2_manifests()
    case = calibration.cases[0]
    metrics = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_error_code=AgentErrorCode.INVALID_PROPOSAL,
            proposal_format_valid=False,
            proposal_repairs=1,
            proposal_format_event_count=2,
        ),
    )

    assert metrics.invalid_proposal_count == 1
    assert metrics.proposal_format_event_count == 2
    assert metrics.failure_class_counts == ((V2FailureClass.PROPOSAL_REPAIR.value, 1),)


def test_v2_environment_failures_do_not_masquerade_as_invalid_proposals() -> None:
    calibration, _ = build_v2_manifests()
    case = calibration.cases[0]
    unavailable = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_error_code=AgentErrorCode.MODEL_UNAVAILABLE,
            proposal_format_valid=False,
            runtime_failure=True,
        ),
    )
    timeout = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_error_code=AgentErrorCode.MODEL_TIMEOUT,
            proposal_format_valid=False,
            runtime_failure=True,
        ),
    )

    assert unavailable.invalid_proposal_count == 0
    assert unavailable.model_unavailable_count == 1
    assert unavailable.proposal_format_validity.denominator == 71
    assert timeout.invalid_proposal_count == 0
    assert timeout.model_timeout_count == 1
    assert timeout.proposal_format_validity.denominator == 71


def test_v2_downstream_rag_failure_is_not_a_proposal_failure() -> None:
    calibration, _ = build_v2_manifests()
    case = next(
        case for case in calibration.cases if case.expected_action is V2ExpectedAction.RETRIEVE
    )
    metrics = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_action=V2ExpectedAction.RETRIEVE,
            observed_error_code=AgentErrorCode.RAG_QUERY_FAILED,
            proposal_format_valid=True,
            runtime_failure=True,
        ),
    )

    assert metrics.invalid_proposal_count == 0
    assert metrics.environment_failure_count == 1
    assert metrics.failure_class_counts == ((V2FailureClass.RAG_FAILURE.value, 1),)


def test_v2_generic_environment_failure_does_not_masquerade_as_invalid_proposal() -> None:
    calibration, _ = build_v2_manifests()
    case = calibration.cases[0]
    metrics = score_v2_evaluation(
        calibration,
        _traces_with_override(
            case.case_id,
            observed_error_code=None,
            proposal_format_valid=False,
            runtime_failure=True,
        ),
    )

    assert metrics.invalid_proposal_count == 0
    assert metrics.environment_failure_count == 1
    assert metrics.proposal_format_validity.denominator == 71
    assert metrics.failure_class_counts == ((V2FailureClass.ENVIRONMENT.value, 1),)
