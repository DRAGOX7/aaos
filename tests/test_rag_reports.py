from __future__ import annotations

import json
from pathlib import Path

import pytest

from aaos.rag.contracts import GlobalScope
from aaos.rag.evaluation.contracts import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationSplit,
    RelevanceJudgment,
)
from aaos.rag.evaluation.metrics import deterministic_split
from aaos.rag.evaluation.reports import (
    HUMAN_RUBRIC_DIMENSIONS,
    SAFE_ACCEPTANCE_FIELDS,
    HumanReviewLabel,
    PerformanceEnvironment,
    PerformanceMeasurement,
    PerformanceSample,
    RubricScore,
    SafetyObservation,
    build_evaluation_summary,
    build_human_report,
    build_performance_report,
    build_safety_report,
    write_evaluation_summary,
)
from aaos.rag.retrieval import SufficiencyStatus


def _dataset() -> EvaluationDataset:
    return EvaluationDataset(
        cases=(
            EvaluationCase(
                case_id="case-en",
                question="private question en",
                query_language="en",
                answer_language="en",
                expected_status=SufficiencyStatus.SUPPORTED,
                scope=GlobalScope(),
                relevance=(RelevanceJudgment(101, (1,), 3),),
                categories=("semantic", "current"),
                authorized_document_version_ids=(101,),
            ),
            EvaluationCase(
                case_id="case-ar-en",
                question="سؤال خاص",
                query_language="ar",
                answer_language="en",
                expected_status=SufficiencyStatus.PARTIALLY_SUPPORTED,
                scope=GlobalScope(),
                relevance=(RelevanceJudgment(102, (2,), 2),),
                categories=("cross-language", "partial"),
                authorized_document_version_ids=(102,),
            ),
        ),
        dataset_version="gold-v1",
    )


def _scores() -> tuple[RubricScore, ...]:
    return tuple(RubricScore(name, 4) for name in HUMAN_RUBRIC_DIMENSIONS)


def _human_labels() -> tuple[HumanReviewLabel, ...]:
    return (
        HumanReviewLabel(
            case_id="case-en",
            query_language="en",
            answer_language="en",
            rubric_scores=_scores(),
            reviewer_identity_hash="a" * 64,
            confidence=0.9,
            observed_status=SufficiencyStatus.SUPPORTED,
        ),
        HumanReviewLabel(
            case_id="case-ar-en",
            query_language="ar",
            answer_language="en",
            rubric_scores=_scores(),
            reviewer_identity_hash="b" * 64,
            confidence=0.8,
            observed_status=SufficiencyStatus.PARTIALLY_SUPPORTED,
        ),
    )


def _performance_sample(
    *, released: bool = True, generation_after_release: bool = True
) -> PerformanceSample:
    environment = PerformanceEnvironment(
        environment_id="windows-local",
        model_identity="bge-m3-revision",
        index_identity="index-fingerprint",
        hardware_identity="rtx4050-6gb",
    )
    return PerformanceSample(
        environment=environment,
        measurements=(
            PerformanceMeasurement("cold_embedding_latency", 12.0, "ms"),
            PerformanceMeasurement("total_latency", 48.0, "ms"),
            PerformanceMeasurement("gpu_memory", 4096.0, "MiB"),
        ),
        embedding_release_verified=released,
        generation_after_embedding_release=generation_after_release,
    )


def test_human_report_contains_required_aggregate_fields_without_private_text() -> None:
    report = build_human_report(_dataset(), _human_labels())

    assert set(report) <= {
        "case_ids",
        "case_count",
        "language_pair_counts",
        "rubric_dimensions",
        "rubric_means",
        "confidence_mean",
        "reviewer_identity_hashes",
        "dataset_fingerprint",
        "split_fingerprint",
        "subgroup_rubric_means",
        "sufficiency_outcomes",
    }
    assert report["case_count"] == 2
    assert report["rubric_dimensions"] == list(HUMAN_RUBRIC_DIMENSIONS)
    outcomes = report["sufficiency_outcomes"]
    assert isinstance(outcomes, dict)
    assert outcomes["supported_acceptance"] == 1
    subgroup = report["subgroup_rubric_means"]
    assert isinstance(subgroup, dict)
    assert subgroup["ar->en"]["arabic_quality"] == 4.0
    rendered = json.dumps(report, ensure_ascii=False)
    assert "private" not in rendered
    assert "سؤال" not in rendered


def test_human_report_rejects_missing_rubric_and_unknown_case() -> None:
    from aaos.rag.evaluation.reports import HumanReviewLabel

    incomplete = HumanReviewLabel(
        case_id="case-en",
        query_language="en",
        answer_language="en",
        rubric_scores=_scores()[:-1],
        reviewer_identity_hash="a" * 64,
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="rubric"):
        build_human_report(_dataset(), (incomplete,))

    unknown = HumanReviewLabel(
        case_id="not-a-case",
        query_language="en",
        answer_language="en",
        rubric_scores=_scores(),
        reviewer_identity_hash="a" * 64,
        confidence=1.0,
    )
    with pytest.raises(ValueError, match="unknown case"):
        build_human_report(_dataset(), (unknown,))


def test_human_report_preserves_locked_split_lineage() -> None:
    dataset = _dataset()
    split = deterministic_split(dataset, seed="reports")
    report = build_human_report(dataset, _human_labels(), split=split)
    assert report["dataset_fingerprint"] == dataset.dataset_fingerprint
    assert isinstance(report["split_fingerprint"], str)

    unlocked = EvaluationSplit(
        calibration_case_ids=split.calibration_case_ids,
        held_out_case_ids=split.held_out_case_ids,
        dataset_fingerprint=split.dataset_fingerprint,
        seed=split.seed,
        held_out_locked=False,
    )
    with pytest.raises(ValueError, match="locked"):
        build_human_report(dataset, _human_labels(), split=unlocked)


def test_safety_report_is_fixed_category_aggregate_and_rejects_unknown_category() -> None:
    observations = (
        SafetyObservation("case-en", "prompt_injection", True),
        SafetyObservation("case-ar-en", "forged_evidence", True),
        SafetyObservation("case-en", "scope_isolation", False),
    )
    report = build_safety_report(_dataset(), observations)
    assert report["case_count"] == 2
    assert report["overall_pass"] is False
    categories = report["categories"]
    assert isinstance(categories, list)
    forged = next(item for item in categories if item["category"] == "forged_evidence")
    assert isinstance(forged, dict)
    assert forged["pass_count"] == 1
    with pytest.raises(ValueError, match="category"):
        SafetyObservation("case-en", "arbitrary_policy", True)


def test_performance_report_requires_units_and_verified_embedding_release() -> None:
    report = build_performance_report((_performance_sample(),))
    assert report["sample_count"] == 1
    assert report["embedding_release_verified"] is True
    assert report["generation_after_embedding_release"] is True
    metrics = report["metrics"]
    assert isinstance(metrics, list)
    assert {item["unit"] for item in metrics} == {"ms", "MiB"}

    with pytest.raises(ValueError, match="release"):
        build_performance_report((_performance_sample(released=False),))
    with pytest.raises(ValueError, match="ordering"):
        build_performance_report((_performance_sample(generation_after_release=False),))
    with pytest.raises(ValueError, match="unit"):
        PerformanceMeasurement("total_latency", 1.0, "seconds")


def test_summary_and_json_artifact_are_deterministic_and_use_injected_root(tmp_path: Path) -> None:
    dataset = _dataset()
    summary = build_evaluation_summary(
        dataset,
        run_id="reports-run",
        human_labels=_human_labels(),
        safety_observations=(SafetyObservation("case-en", "refusal", True),),
        performance_samples=(_performance_sample(),),
    )
    assert set(summary) <= SAFE_ACCEPTANCE_FIELDS
    assert summary["dataset_fingerprint"] == dataset.dataset_fingerprint

    first = write_evaluation_summary(tmp_path / "evaluation-output", summary)
    second = write_evaluation_summary(tmp_path / "evaluation-output", summary)
    assert first == second
    assert first.parent == tmp_path / "evaluation-output"
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert "private question" not in first.read_text(encoding="utf-8")


def test_summary_rejects_mismatched_ragas_lineage() -> None:
    from aaos.rag.evaluation.ragas_adapter import (
        RAGAS_METRICS,
        RAGAS_VERSION,
        RagasEvaluationResult,
        RagasMetricValue,
    )

    result = RagasEvaluationResult(
        run_id="ragas-run",
        ragas_version=RAGAS_VERSION,
        evaluator_model="local-judge",
        evaluator_embeddings="local-embeddings",
        metric_set=RAGAS_METRICS,
        metric_set_version="v1",
        dataset_version="gold-v1",
        dataset_fingerprint="0" * 64,
        case_count=2,
        language_counts=(("ar", 1), ("en", 1)),
        metrics=tuple(RagasMetricValue(name, 0.5) for name in RAGAS_METRICS),
    )
    with pytest.raises(ValueError, match="fingerprint"):
        build_evaluation_summary(_dataset(), run_id="reports-run", ragas_result=result)


def test_serializer_rejects_nested_private_fields() -> None:
    summary = build_evaluation_summary(_dataset(), run_id="reports-run")
    summary["human"] = {"question": "private question"}
    with pytest.raises(ValueError, match="safe report schema"):
        from aaos.rag.evaluation.reports import serialize_evaluation_summary

        serialize_evaluation_summary(summary)


def test_serializer_rejects_private_values_in_allowed_fields() -> None:
    summary = build_evaluation_summary(
        _dataset(), run_id="reports-run", human_labels=_human_labels()
    )
    human = summary["human"]
    assert isinstance(human, dict)
    human["case_ids"] = ["private question", "case-ar-en"]
    with pytest.raises(ValueError, match="safe report"):
        from aaos.rag.evaluation.reports import serialize_evaluation_summary

        serialize_evaluation_summary(summary)


def test_safe_metadata_rejects_prose_and_paths() -> None:
    with pytest.raises(ValueError):
        PerformanceEnvironment("human readable environment", "model", "index", "hardware")
    with pytest.raises(ValueError):
        PerformanceEnvironment("environment", "C:\\private\\model", "index", "hardware")
