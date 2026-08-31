"""Task 22 deterministic information-retrieval evaluation tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aaos.rag.contracts import CourseScope, GlobalScope, HistoricalScope, RagScope
from aaos.rag.evaluation.contracts import (
    AblationConfig,
    EvaluationCase,
    EvaluationDataset,
    EvaluationExpectedStatus,
    RelevanceJudgment,
    RetrievalMode,
    RetrievalRun,
    SufficiencyObservation,
)
from aaos.rag.evaluation.metrics import (
    compute_metrics,
    compute_ndcg,
    compute_recall,
    compute_sufficiency_metrics,
    default_ablation_configs,
    deterministic_split,
    evaluate_ablation,
    evaluate_retrieval,
)
from aaos.rag.retrieval import SufficiencyStatus


def _case(
    case_id: str,
    *,
    query_language: str = "en",
    answer_language: str = "en",
    expected_status: SufficiencyStatus = SufficiencyStatus.SUPPORTED,
    scope: RagScope | None = None,
    relevant: tuple[RelevanceJudgment, ...] = (RelevanceJudgment(101, (1,), 3),),
    categories: tuple[str, ...] = ("lexical", "current"),
    authorized: tuple[int, ...] = (101, 102, 103),
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        question=f"Synthetic evaluation question {case_id}",
        query_language=query_language,
        answer_language=answer_language,
        expected_status=expected_status,
        scope=GlobalScope() if scope is None else scope,
        relevance=relevant,
        categories=categories,
        authorized_document_version_ids=authorized,
    )


def _dataset(cases: tuple[EvaluationCase, ...]) -> EvaluationDataset:
    return EvaluationDataset(cases=cases, dataset_version="test-v1")


def test_case_rejects_invalid_relevance_and_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        RelevanceJudgment(101, (), 3)
    with pytest.raises(ValueError):
        _case(
            "duplicate-relevance",
            relevant=(RelevanceJudgment(101, (1,), 3), RelevanceJudgment(101, (2,), 1)),
        )
    with pytest.raises(ValueError):
        _dataset((_case("same"), _case("same")))


def test_target_dataset_requires_size_and_required_coverage() -> None:
    cases = tuple(
        replace(
            _case(
                f"case-{index}",
                query_language="ar" if index % 2 else "en",
                answer_language="en" if index % 3 == 0 else "ar",
                categories=(
                    "lexical",
                    "semantic",
                    "multi-source",
                    "conflicting",
                    "partial",
                    "unsupported",
                    "current",
                    "historical",
                    "course-isolation",
                    "cross-language",
                ),
            ),
            case_id=f"case-{index}",
        )
        for index in range(80)
    )
    dataset = _dataset(cases)
    dataset.validate_target_set()
    with pytest.raises(ValueError):
        _dataset(cases[:79]).validate_target_set()


def test_split_is_stable_disjoint_and_held_out_is_locked() -> None:
    cases = tuple(_case(f"case-{index}") for index in range(10))
    first = deterministic_split(_dataset(cases), seed="fixed")
    second = deterministic_split(_dataset(tuple(reversed(cases))), seed="fixed")
    assert first == second
    assert set(first.calibration_case_ids).isdisjoint(first.held_out_case_ids)
    assert first.held_out_locked is True
    assert len(first.calibration_case_ids) + len(first.held_out_case_ids) == 10
    assert 0.6 <= len(first.calibration_case_ids) / 10 <= 0.8


def test_recall_deduplicates_ranked_candidates_and_zero_relevance_is_explicit() -> None:
    assert compute_recall(("a", "a", "b"), ("a", "b"), k=3) == 1.0
    assert compute_recall(("a", "a", "b"), ("a", "b"), k=1) == 0.5
    assert compute_recall(("a",), (), k=1) == 0.0


def test_ndcg_uses_graded_judgments_and_exact_cutoff() -> None:
    judgments = {"a": 3.0, "b": 1.0}
    assert compute_ndcg(("a", "b"), judgments, k=2) == pytest.approx(1.0)
    assert compute_ndcg(("b", "a"), judgments, k=1) == pytest.approx(1.0 / 7.0)
    assert compute_ndcg(("a",), {}, k=1) == 0.0


def test_modes_are_exact_and_ablation_values_are_controlled() -> None:
    assert {item.value for item in RetrievalMode} == {
        "DENSE_ONLY",
        "FTS5_ONLY",
        "HYBRID_RRF",
    }
    assert tuple(item.chunk_target_tokens for item in default_ablation_configs()) == (300, 500, 700)
    with pytest.raises(ValueError):
        AblationConfig(301)


def test_ablation_evaluation_requires_exact_controlled_targets() -> None:
    dataset = _dataset((_case("one"),))
    runs = {
        target: (RetrievalRun("one", RetrievalMode.HYBRID_RRF, (101,), target),)
        for target in (300, 500, 700)
    }
    report = evaluate_ablation(dataset, runs)
    assert tuple(item.chunk_target_tokens for item in report.configurations) == (300, 500, 700)
    assert len(report.metrics) == 3


def test_evaluation_rejects_scope_leakage_and_reports_all_modes() -> None:
    cases = (
        _case("one", authorized=(101, 102)),
        _case("two", authorized=(201, 202), relevant=(RelevanceJudgment(201, (1,), 3),)),
    )
    dataset = _dataset(cases)
    valid_runs = tuple(
        RetrievalRun(case.case_id, mode, (101 if case.case_id == "one" else 201,))
        for mode in RetrievalMode
        for case in cases
    )
    reports = evaluate_retrieval(dataset, valid_runs)
    assert tuple(report.mode for report in reports) == tuple(RetrievalMode)
    leaked = replace(valid_runs[0], ranked_document_version_ids=(999,))
    with pytest.raises(ValueError):
        evaluate_retrieval(dataset, (leaked, *valid_runs[1:]))


def test_metrics_include_required_cutoffs_and_sufficiency_rates() -> None:
    cases = (
        _case("supported"),
        _case(
            "unsupported",
            expected_status=SufficiencyStatus.UNSUPPORTED,
            relevant=(),
            authorized=(),
            categories=("unsupported",),
        ),
    )
    dataset = _dataset(cases)
    runs = tuple(
        RetrievalRun(
            case.case_id, RetrievalMode.HYBRID_RRF, (101,) if case.case_id == "supported" else ()
        )
        for case in cases
    )
    metrics = compute_metrics(dataset, runs)
    assert tuple(item.k for item in metrics.recall_at_k) == (1, 3, 5, 10)
    assert metrics.evaluated_query_count == 1
    assert metrics.mrr == 1.0
    assert metrics.ndcg_at_k[0].value == 1.0
    sufficiency = compute_sufficiency_metrics(
        dataset,
        (
            SufficiencyObservation("supported", SufficiencyStatus.SUPPORTED),
            SufficiencyObservation("unsupported", SufficiencyStatus.UNSUPPORTED),
        ),
    )
    assert sufficiency.false_acceptance_rate == 0.0
    assert sufficiency.false_refusal_rate == 0.0


def test_manifest_is_fingerprinted_without_private_question_text() -> None:
    from aaos.rag.evaluation.metrics import build_safe_run_manifest

    dataset = _dataset((_case("one"),))
    split = deterministic_split(dataset)
    manifest = build_safe_run_manifest(dataset, split, (RetrievalMode.HYBRID_RRF,))
    assert "Synthetic evaluation question" not in repr(manifest)
    assert len(manifest.dataset_fingerprint) == 64
    assert manifest.modes == (RetrievalMode.HYBRID_RRF,)


def test_scope_contract_accepts_course_global_and_historical() -> None:
    assert isinstance(_case("course", scope=CourseScope(7)).scope, CourseScope)
    assert isinstance(_case("global", scope=GlobalScope()).scope, GlobalScope)
    assert isinstance(_case("history", scope=HistoricalScope((11,))).scope, HistoricalScope)
    assert EvaluationExpectedStatus is SufficiencyStatus
