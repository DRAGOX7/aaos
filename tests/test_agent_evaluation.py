"""Slice 8 agent-evaluation contract and metric tests."""

from __future__ import annotations

import json

import pytest

from aaos.agent.contracts import AgentErrorCode, TerminalResultKind, ToolName
from aaos.agent.evaluation.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    METRIC_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkCategory,
    EvaluationLanguage,
    EvaluationManifest,
    EvaluationSplit,
    FinalHeldoutLedger,
    ScopeOracle,
    build_approved_manifest,
)
from aaos.agent.evaluation.metrics import (
    EvaluationTrace,
    score_evaluation,
)
from aaos.agent.evaluation.reports import (
    build_evaluation_report,
    build_private_bundle,
)


def _case(
    case_id: str = "dev-001",
    *,
    split: EvaluationSplit = EvaluationSplit.DEVELOPMENT,
    category: BenchmarkCategory = BenchmarkCategory.RAG_QUERY,
    language: EvaluationLanguage = EvaluationLanguage.ENGLISH,
    expected_tool: ToolName | None = ToolName.RAG_QUERY,
    expected_terminal: TerminalResultKind | None = TerminalResultKind.GROUNDED_RAG_RESPONSE,
) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        split=split,
        category=category,
        language=language,
        request_text="Answer this AAOS request using the permitted workflow.",
        expected_tool_name=expected_tool,
        expected_terminal_kind=expected_terminal,
        tool_selection_opportunity=expected_tool is not None,
        scope_oracle=ScopeOracle.NONE,
        expected_no_tool=expected_tool is None and expected_terminal is None,
    )


def _manifest(*cases: BenchmarkCase) -> EvaluationManifest:
    return EvaluationManifest(
        benchmark_version="test-v1",
        cases=tuple(cases),
    )


def _trace(case_id: str, *, tool: ToolName | None = ToolName.RAG_QUERY) -> EvaluationTrace:
    return EvaluationTrace(
        case_id=case_id,
        observed_tool_names=() if tool is None else (tool,),
        observed_terminal_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
    )


def test_approved_manifest_has_frozen_counts_coverage_and_disjoint_splits() -> None:
    manifest = build_approved_manifest()
    assert manifest.schema_version == BENCHMARK_SCHEMA_VERSION
    assert manifest.calibration_case_count == 72
    assert manifest.final_heldout_case_count == 24
    assert manifest.language_counts(EvaluationSplit.CALIBRATION) == {"en": 36, "ar": 36}
    assert manifest.language_counts(EvaluationSplit.FINAL_HELDOUT) == {"en": 12, "ar": 12}
    assert manifest.case_ids == tuple(case.case_id for case in manifest.cases)
    assert len(set(manifest.case_ids)) == len(manifest.case_ids)
    heldout_categories = {case.category for case in manifest.cases[72:]}
    assert {
        BenchmarkCategory.COURSE_SCOPE,
        BenchmarkCategory.GLOBAL_SCOPE,
        BenchmarkCategory.HISTORICAL_SCOPE,
        BenchmarkCategory.SUPPORTED_GROUNDED,
        BenchmarkCategory.INSUFFICIENT_EVIDENCE,
        BenchmarkCategory.UNKNOWN_TOOL,
        BenchmarkCategory.MALFORMED_ARGUMENTS,
        BenchmarkCategory.MISSING_ARGUMENTS,
        BenchmarkCategory.EXTRA_ARGUMENTS,
        BenchmarkCategory.PERMISSION_DENIAL,
        BenchmarkCategory.READ_ONLY_BEHAVIOR,
        BenchmarkCategory.ALLOWED_LOCAL_STATE_WRITE,
        BenchmarkCategory.FORBIDDEN_HOST_FILE,
        BenchmarkCategory.FORBIDDEN_CODE_EXECUTION,
        BenchmarkCategory.MODEL_UNAVAILABLE,
        BenchmarkCategory.MODEL_TIMEOUT,
        BenchmarkCategory.TOOL_FAILURE,
    } <= heldout_categories


def test_model_visible_input_excludes_oracle_fields() -> None:
    case = _case()
    visible = case.model_visible_input()
    assert visible == {"request_text": case.request_text}
    assert "expected_tool_name" not in visible
    assert "scope_oracle" not in visible
    assert "terminal" not in json.dumps(visible).lower()


def test_manifest_rejects_duplicate_ids_and_invalid_categories() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _manifest(_case("same"), _case("same"))
    with pytest.raises(ValueError, match="category"):
        BenchmarkCase(
            case_id="bad-category",
            split=EvaluationSplit.DEVELOPMENT,
            category="not-a-category",  # type: ignore[arg-type]
            language=EvaluationLanguage.ENGLISH,
            request_text="request",
        )


def test_scope_oracle_is_allowlisted_and_unknown_values_rejected() -> None:
    with pytest.raises(ValueError, match="scope"):
        BenchmarkCase(
            case_id="bad-scope",
            split=EvaluationSplit.DEVELOPMENT,
            category=BenchmarkCategory.RAG_QUERY,
            language=EvaluationLanguage.ENGLISH,
            request_text="request",
            scope_oracle="../../secret",  # type: ignore[arg-type]
        )


def test_trace_identity_and_observed_contract_are_strict() -> None:
    with pytest.raises(ValueError, match="safe logical"):
        EvaluationTrace(case_id="../../private", observed_tool_names=())
    with pytest.raises(ValueError, match="tool_call_count"):
        EvaluationTrace(case_id="safe", observed_tool_call_count=-1)
    trace = EvaluationTrace(
        case_id="safe",
        observed_scope=ScopeOracle.COURSE,
        observed_tool_call_count=1,
        observed_grounded=True,
        observed_permission_allowed=True,
        runtime_limit_bypass=True,
    )
    assert trace.to_dict()["observed_scope"] == "course"
    assert trace.to_dict()["security_flags"] == {"runtime_limit_bypass": True}


def test_final_heldout_freezes_and_can_be_consumed_only_once() -> None:
    manifest = build_approved_manifest()
    registry: set[str] = set()
    ledger = FinalHeldoutLedger(manifest, consumption_registry=registry)
    ledger.freeze()
    with pytest.raises(ValueError, match="frozen"):
        ledger.replace_manifest(manifest)
    consumed = ledger.consume()
    assert consumed == tuple(
        case for case in manifest.cases if case.split is EvaluationSplit.FINAL_HELDOUT
    )
    with pytest.raises(ValueError, match="already consumed"):
        ledger.consume()
    second_ledger = FinalHeldoutLedger(manifest, consumption_registry=registry)
    second_ledger.freeze()
    with pytest.raises(ValueError, match="already consumed"):
        second_ledger.consume()


def test_final_heldout_cannot_register_phase4_dataset_as_fresh() -> None:
    phase4_like = _case("phase4-heldout", split=EvaluationSplit.FINAL_HELDOUT)
    manifest = EvaluationManifest(
        benchmark_version="phase4-real-retrieval-v3-semantic",
        cases=(phase4_like,),
        source_phase="phase4",
    )
    with pytest.raises(ValueError, match="Phase 4"):
        FinalHeldoutLedger(manifest, consumption_registry=set())


def test_final_heldout_ledger_rejects_non_approved_split_shape() -> None:
    manifest = _manifest(_case("final-001", split=EvaluationSplit.FINAL_HELDOUT))
    manifest = EvaluationManifest(benchmark_version="phase5-agent-aaos-v1", cases=manifest.cases)
    with pytest.raises(ValueError, match="approved Phase 5 split"):
        FinalHeldoutLedger(manifest, consumption_registry=set())


def test_score_is_deterministic_with_exact_tool_denominator_and_security_zeroes() -> None:
    cases = (
        _case("tool", expected_tool=ToolName.RAG_QUERY),
        _case("refusal", expected_tool=None, expected_terminal=None),
    )
    manifest = _manifest(*cases)
    traces = (
        _trace("tool"),
        EvaluationTrace(
            case_id="refusal",
            observed_tool_names=(),
            observed_terminal_kind=None,
        ),
    )
    first = score_evaluation(manifest, traces)
    second = score_evaluation(manifest, traces)
    assert first == second
    assert first.tool_selection.numerator == 1
    assert first.tool_selection.denominator == 1
    assert first.hard_gate_violations == 0


def test_hard_gate_violation_forces_failure_and_is_not_averaged_away() -> None:
    manifest = _manifest(_case("one"))
    trace = EvaluationTrace(
        case_id="one",
        observed_tool_names=(ToolName.RAG_QUERY,),
        observed_terminal_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
        unauthorized_tool_execution=True,
    )
    metrics = score_evaluation(manifest, (trace,))
    assert metrics.hard_gate_violations == 1
    assert metrics.acceptance_status == "FAIL"
    assert metrics.runtime_limit_bypass_count == 0


def test_all_privacy_and_lifecycle_hard_gate_flags_are_scored() -> None:
    manifest = _manifest(_case("one"))
    trace = EvaluationTrace(
        case_id="one",
        observed_tool_names=(ToolName.RAG_QUERY,),
        observed_terminal_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
        raw_result_fallback=True,
        invalid_output_exposure=True,
        secret_exposure=True,
        operational_path_exposure=True,
        raw_exception_exposure=True,
        contradictory_late_success=True,
        event_privacy_violation=True,
    )
    metrics = score_evaluation(manifest, (trace,))
    assert metrics.raw_result_fallback_count == 1
    assert metrics.event_privacy_violation_count == 1
    assert metrics.acceptance_status == "FAIL"


def test_scope_grounding_permission_and_bound_oracles_are_scored() -> None:
    cases = (
        BenchmarkCase(
            case_id="scope",
            split=EvaluationSplit.DEVELOPMENT,
            category=BenchmarkCategory.COURSE_SCOPE,
            language=EvaluationLanguage.ENGLISH,
            request_text="course scope",
            expected_tool_name=ToolName.RAG_QUERY,
            scope_oracle=ScopeOracle.COURSE,
            tool_selection_opportunity=True,
            expected_grounded=True,
        ),
        BenchmarkCase(
            case_id="bound",
            split=EvaluationSplit.DEVELOPMENT,
            category=BenchmarkCategory.REPEATED_TOOL_CALL,
            language=EvaluationLanguage.ENGLISH,
            request_text="bounded repetition",
            expected_tool_name=ToolName.RAG_QUERY,
            tool_selection_opportunity=True,
            expected_max_tool_calls=2,
        ),
        BenchmarkCase(
            case_id="permission",
            split=EvaluationSplit.DEVELOPMENT,
            category=BenchmarkCategory.PERMISSION_DENIAL,
            language=EvaluationLanguage.ENGLISH,
            request_text="permission denial",
            expected_tool_name=ToolName.WRITE_TASK_STATE,
            expected_error_code=AgentErrorCode.PERMISSION_DENIED,
            tool_selection_opportunity=True,
            expected_permission_allowed=False,
        ),
    )
    manifest = _manifest(*cases)
    traces = (
        EvaluationTrace(
            case_id="scope",
            observed_tool_names=(ToolName.RAG_QUERY,),
            observed_scope=ScopeOracle.COURSE,
            observed_grounded=True,
        ),
        EvaluationTrace(
            case_id="bound",
            observed_tool_names=(ToolName.RAG_QUERY,),
            observed_tool_call_count=2,
        ),
        EvaluationTrace(
            case_id="permission",
            observed_tool_names=(ToolName.WRITE_TASK_STATE,),
            observed_error_code=AgentErrorCode.PERMISSION_DENIED,
            observed_permission_allowed=False,
        ),
    )
    metrics = score_evaluation(manifest, traces)
    assert metrics.case_pass.numerator == 3


def test_unknown_tool_and_invalid_trace_rejected_before_scoring() -> None:
    manifest = _manifest(_case("one"))
    with pytest.raises(ValueError, match="unknown case"):
        score_evaluation(manifest, (_trace("missing"),))
    with pytest.raises(ValueError, match="duplicate"):
        score_evaluation(manifest, (_trace("one"), _trace("one")))


def test_private_bundle_and_result_have_versions_exact_counts_and_no_raw_content() -> None:
    manifest = _manifest(_case("one"))
    trace = _trace("one")
    bundle = build_private_bundle(manifest, (trace,))
    report = build_evaluation_report(
        manifest,
        (trace,),
        runtime_policy_version="phase5-runtime-v1",
        runtime_policy_fingerprint="a" * 64,
    )
    assert bundle.schema_version == METRIC_SCHEMA_VERSION
    assert report.schema_version == METRIC_SCHEMA_VERSION
    rendered = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "Answer this AAOS request" not in rendered
    assert "request_text" not in rendered
    assert report.metrics.tool_selection.denominator == 1
    assert report.metadata.event_schema_version == EVENT_SCHEMA_VERSION
    assert report.metadata.runtime_policy_fingerprint == "a" * 64
    assert bundle.to_dict()["trace_schema_version"] == "phase5-agent-trace-v1"


def test_private_bundle_rejects_unretained_raw_fields() -> None:
    manifest = _manifest(_case("one"))
    with pytest.raises(ValueError, match="trusted evaluator"):
        from aaos.agent.evaluation.reports import PrivateEvaluationBundle

        PrivateEvaluationBundle(
            benchmark_fingerprint=manifest.dataset_fingerprint,
            split_fingerprint=manifest.split_fingerprint,
            manifest_case_ids=("one",),
            rows=(
                {
                    "case_id": "one",
                    "language": "en",
                    "split": "development",
                    "request_text": "secret private prompt",
                },
            ),
        )


def test_private_bundle_requires_trusted_factory_and_exact_row_order() -> None:
    manifest = _manifest(_case("one"), _case("two"))
    trace = _trace("one")
    with pytest.raises(ValueError, match="trusted evaluator"):
        from aaos.agent.evaluation.reports import PrivateEvaluationBundle

        PrivateEvaluationBundle(
            benchmark_fingerprint=manifest.dataset_fingerprint,
            split_fingerprint=manifest.split_fingerprint,
            manifest_case_ids=("one", "two"),
            rows=({"case_id": "one"}, {"case_id": "two"}),
        )
    bundle = build_private_bundle(manifest, (trace, _trace("two")))
    assert tuple(row["case_id"] for row in bundle.rows) == ("one", "two")
    assert "category" in bundle.rows[0]
    assert "requires_structured_repair" in bundle.rows[0]


def test_private_bundle_is_deeply_immutable_and_fingerprint_stable() -> None:
    manifest = _manifest(_case("one"))
    bundle = build_private_bundle(manifest, (_trace("one"),))
    fingerprint = bundle.fingerprint
    with pytest.raises(TypeError):
        bundle.rows[0]["observed_tool_names"] = ("write_task_state",)  # type: ignore[index]
    with pytest.raises(TypeError):
        flags = bundle.rows[0]["security_flags"]
        flags["secret_exposure"] = True  # type: ignore[index]
    assert bundle.fingerprint == fingerprint


def test_security_fields_and_language_are_retained_without_private_payloads() -> None:
    manifest = _manifest(_case("ar", language=EvaluationLanguage.ARABIC))
    trace = EvaluationTrace(
        case_id="ar",
        observed_tool_names=(),
        observed_terminal_kind=None,
        prompt_injection_policy_bypass=True,
        model_timeout=True,
    )
    report = build_evaluation_report(
        manifest,
        (trace,),
        runtime_policy_version="phase5-runtime-v1",
        runtime_policy_fingerprint="b" * 64,
    )
    assert report.metrics.prompt_injection_policy_bypass_count == 1
    assert report.metrics.model_timeout_count == 1
    assert report.trace_summaries[0]["language"] == "ar"
    assert report.trace_summaries[0]["category"] == BenchmarkCategory.RAG_QUERY.value


def test_import_boundary_has_no_runtime_side_effects() -> None:
    import aaos.agent.evaluation as evaluation

    assert evaluation.__name__ == "aaos.agent.evaluation"
    assert "ModelGateway" not in vars(evaluation)
