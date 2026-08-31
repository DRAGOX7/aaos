"""Sanitized, deterministic Phase 4 evaluation reports.

This module accepts only aggregate evaluation records and writes only an
allowlisted JSON summary to an explicitly injected output root.  Questions,
answers, retrieved chunks, document titles, and evaluator payloads never cross
the reporting boundary.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Final, TypeAlias

from ..retrieval import SufficiencyStatus
from .contracts import EvaluationDataset, EvaluationSplit, SafeRunManifest
from .ragas_adapter import RagasEvaluationResult

SAFE_ACCEPTANCE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "run_id",
        "dataset_version",
        "dataset_fingerprint",
        "split_fingerprint",
        "retrieval_manifest",
        "human",
        "safety",
        "performance",
        "ragas",
    }
)

HUMAN_RUBRIC_DIMENSIONS: Final = (
    "answer_correctness",
    "groundedness",
    "usefulness",
    "citation_usefulness",
    "claim_evidence_support",
    "citation_appropriateness",
    "completeness",
    "arabic_quality",
    "partial_answer_quality",
    "conflict_handling",
    "refusal_quality",
)

SAFETY_CATEGORIES: Final = (
    "prompt_injection",
    "forged_evidence",
    "scope_isolation",
    "unsupported_claim",
    "citation_integrity",
    "refusal",
    "no_tools_or_fallback",
    "private_data_leakage",
    "cross_course_isolation",
    "historical_current_isolation",
)

PERFORMANCE_METRIC_UNITS: Final = {
    "indexing_latency": "ms",
    "cold_embedding_latency": "ms",
    "warm_embedding_latency": "ms",
    "dense_retrieval_latency": "ms",
    "fts_retrieval_latency": "ms",
    "rrf_selection_latency": "ms",
    "generation_latency": "ms",
    "total_latency": "ms",
    "indexing_throughput": "docs_per_s",
    "ram": "MiB",
    "gpu_memory": "MiB",
    "chroma_size": "MiB",
}

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,199}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")

_HUMAN_FIELDS = frozenset(
    {
        "dataset_fingerprint",
        "split_fingerprint",
        "case_ids",
        "case_count",
        "language_pair_counts",
        "rubric_dimensions",
        "rubric_means",
        "subgroup_rubric_means",
        "confidence_mean",
        "reviewer_identity_hashes",
        "sufficiency_outcomes",
    }
)
_SAFETY_FIELDS = frozenset(
    {
        "dataset_fingerprint",
        "split_fingerprint",
        "case_ids",
        "case_count",
        "observation_count",
        "overall_pass",
        "categories",
    }
)
_PERFORMANCE_FIELDS = frozenset(
    {
        "sample_count",
        "environment",
        "metrics",
        "unmeasured_metrics",
        "embedding_release_verified",
        "generation_after_embedding_release",
    }
)
_RAGAS_FIELDS = frozenset(
    {
        "run_id",
        "ragas_version",
        "evaluator_model",
        "evaluator_embeddings",
        "metric_set",
        "metric_set_version",
        "dataset_version",
        "dataset_fingerprint",
        "case_count",
        "language_counts",
        "metrics",
        "split_fingerprint",
    }
)
_RETRIEVAL_MANIFEST_FIELDS = frozenset(
    {
        "dataset_fingerprint",
        "split_fingerprint",
        "calibration_case_count",
        "held_out_case_count",
        "modes",
        "cutoffs",
        "ablation_chunk_targets",
    }
)

EvaluationSummary: TypeAlias = dict[str, object]


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or not _SAFE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a safe non-blank identifier")
    if "://" in normalized or normalized.startswith(("/", "\\")):
        raise ValueError(f"{name} must not contain a URL or absolute path")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise ValueError(f"{name} must not contain a drive path")
    return normalized


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _HEX_DIGEST.fullmatch(value.casefold()):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value.casefold()


def _split_fingerprint(split: EvaluationSplit) -> str:
    payload = json.dumps(
        {
            "dataset": split.dataset_fingerprint,
            "seed": split.seed,
            "calibration": split.calibration_case_ids,
            "held_out": split.held_out_case_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _lineage(dataset: EvaluationDataset, split: EvaluationSplit | None) -> dict[str, str | None]:
    if not isinstance(dataset, EvaluationDataset):
        raise ValueError("dataset must be an EvaluationDataset")
    if split is None:
        return {"dataset_fingerprint": dataset.dataset_fingerprint, "split_fingerprint": None}
    if not isinstance(split, EvaluationSplit):
        raise ValueError("split must be an EvaluationSplit")
    if split.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("split does not belong to dataset")
    if not split.held_out_locked:
        raise ValueError("held-out split must be locked")
    case_ids = {case.case_id for case in dataset.cases}
    if (
        not set(split.calibration_case_ids) <= case_ids
        or not set(split.held_out_case_ids) <= case_ids
    ):
        raise ValueError("split references unknown cases")
    return {
        "dataset_fingerprint": dataset.dataset_fingerprint,
        "split_fingerprint": _split_fingerprint(split),
    }


@dataclass(frozen=True, slots=True)
class RubricScore:
    """One bounded human score; no question or answer text is accepted."""

    dimension: str
    score: int

    def __post_init__(self) -> None:
        if self.dimension not in HUMAN_RUBRIC_DIMENSIONS:
            raise ValueError("rubric dimension is not approved")
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, int)
            or not 1 <= self.score <= 5
        ):
            raise ValueError("rubric score must be an integer from 1 to 5")


@dataclass(frozen=True, slots=True)
class HumanReviewLabel:
    """Safe human label keyed to an evaluation case and reviewer hash."""

    case_id: str
    query_language: str
    answer_language: str
    rubric_scores: tuple[RubricScore, ...]
    reviewer_identity_hash: str
    confidence: float
    observed_status: SufficiencyStatus = SufficiencyStatus.SUPPORTED

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id, "case_id"))
        for field_name in ("query_language", "answer_language"):
            value = _identifier(getattr(self, field_name), field_name).casefold()
            if value not in {"en", "ar"}:
                raise ValueError(f"{field_name} must be 'en' or 'ar'")
            object.__setattr__(self, field_name, value)
        if not isinstance(self.rubric_scores, tuple) or not all(
            isinstance(score, RubricScore) for score in self.rubric_scores
        ):
            raise ValueError("rubric_scores must contain RubricScore values")
        object.__setattr__(
            self,
            "reviewer_identity_hash",
            _digest(self.reviewer_identity_hash, "reviewer_identity_hash"),
        )
        if not isinstance(self.observed_status, SufficiencyStatus):
            raise ValueError("observed_status must be a SufficiencyStatus")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        confidence = float(self.confidence)
        if not isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and between zero and one")
        object.__setattr__(self, "confidence", confidence)


def build_human_report(
    dataset: EvaluationDataset,
    labels: Sequence[HumanReviewLabel],
    *,
    split: EvaluationSplit | None = None,
) -> dict[str, object]:
    """Build aggregate-only human-review output with deterministic ordering."""

    lineage = _lineage(dataset, split)
    values = tuple(labels)
    if not values or not all(isinstance(label, HumanReviewLabel) for label in values):
        raise ValueError("at least one HumanReviewLabel is required")
    cases = {case.case_id: case for case in dataset.cases}
    if len({label.case_id for label in values}) != len(values):
        raise ValueError("human labels must have unique case IDs")
    for label in values:
        case = cases.get(label.case_id)
        if case is None:
            raise ValueError("human label references an unknown case")
        dimensions = tuple(score.dimension for score in label.rubric_scores)
        if set(dimensions) != set(HUMAN_RUBRIC_DIMENSIONS) or len(dimensions) != len(
            set(dimensions)
        ):
            raise ValueError("rubric scores must contain every approved dimension exactly once")
        if (label.query_language, label.answer_language) != (
            case.query_language,
            case.answer_language,
        ):
            raise ValueError("human label language does not match its case")
        if split is not None and label.case_id not in set(split.calibration_case_ids) | set(
            split.held_out_case_ids
        ):
            raise ValueError("human label is outside the locked split")

    means = {
        dimension: round(
            sum(
                next(score.score for score in label.rubric_scores if score.dimension == dimension)
                for label in values
            )
            / len(values),
            6,
        )
        for dimension in HUMAN_RUBRIC_DIMENSIONS
    }
    pair_counts = Counter((label.query_language, label.answer_language) for label in values)
    subgroup_means: dict[str, dict[str, float]] = {}
    for pair in sorted(pair_counts):
        pair_values = tuple(
            label for label in values if (label.query_language, label.answer_language) == pair
        )
        subgroup_means[f"{pair[0]}->{pair[1]}"] = {
            dimension: round(
                sum(
                    next(
                        score.score for score in label.rubric_scores if score.dimension == dimension
                    )
                    for label in pair_values
                )
                / len(pair_values),
                6,
            )
            for dimension in HUMAN_RUBRIC_DIMENSIONS
        }
    outcomes = {
        "supported_acceptance": 0,
        "unsupported_rejection": 0,
        "false_acceptance": 0,
        "false_refusal": 0,
        "partial_expected": 0,
        "partial_observed": 0,
    }
    for label in values:
        expected = cases[label.case_id].expected_status
        observed = label.observed_status
        if expected is SufficiencyStatus.SUPPORTED and observed is SufficiencyStatus.SUPPORTED:
            outcomes["supported_acceptance"] += 1
        elif (
            expected is SufficiencyStatus.UNSUPPORTED and observed is SufficiencyStatus.UNSUPPORTED
        ):
            outcomes["unsupported_rejection"] += 1
        elif expected is SufficiencyStatus.UNSUPPORTED and observed is SufficiencyStatus.SUPPORTED:
            outcomes["false_acceptance"] += 1
        elif expected is SufficiencyStatus.SUPPORTED and observed is SufficiencyStatus.UNSUPPORTED:
            outcomes["false_refusal"] += 1
        if expected is SufficiencyStatus.PARTIALLY_SUPPORTED:
            outcomes["partial_expected"] += 1
        if observed is SufficiencyStatus.PARTIALLY_SUPPORTED:
            outcomes["partial_observed"] += 1
    return {
        **lineage,
        "case_ids": sorted(label.case_id for label in values),
        "case_count": len(values),
        "language_pair_counts": [
            {
                "query_language": query,
                "answer_language": answer,
                "count": pair_counts[(query, answer)],
            }
            for query, answer in sorted(pair_counts)
        ],
        "rubric_dimensions": list(HUMAN_RUBRIC_DIMENSIONS),
        "rubric_means": means,
        "subgroup_rubric_means": subgroup_means,
        "confidence_mean": round(sum(label.confidence for label in values) / len(values), 6),
        "reviewer_identity_hashes": sorted({label.reviewer_identity_hash for label in values}),
        "sufficiency_outcomes": outcomes,
    }


@dataclass(frozen=True, slots=True)
class SafetyObservation:
    """One fixed-category adversarial/safety outcome without payload text."""

    case_id: str
    category: str
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id, "case_id"))
        if self.category not in SAFETY_CATEGORIES:
            raise ValueError("safety category is not approved")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")


def build_safety_report(
    dataset: EvaluationDataset,
    observations: Sequence[SafetyObservation],
    *,
    split: EvaluationSplit | None = None,
) -> dict[str, object]:
    """Build fixed-category safety aggregates; failures remain visible."""

    lineage = _lineage(dataset, split)
    values = tuple(observations)
    if not values or not all(isinstance(item, SafetyObservation) for item in values):
        raise ValueError("at least one SafetyObservation is required")
    known_cases = {case.case_id for case in dataset.cases}
    if any(item.case_id not in known_cases for item in values):
        raise ValueError("safety observation references an unknown case")
    keys = [(item.case_id, item.category) for item in values]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate safety observations are not allowed")
    categories = []
    for category in SAFETY_CATEGORIES:
        category_values = tuple(item for item in values if item.category == category)
        categories.append(
            {
                "category": category,
                "observed_count": len(category_values),
                "pass_count": sum(item.passed for item in category_values),
                "fail_count": sum(not item.passed for item in category_values),
            }
        )
    return {
        **lineage,
        "case_ids": sorted({item.case_id for item in values}),
        "case_count": len({item.case_id for item in values}),
        "observation_count": len(values),
        "overall_pass": all(item.passed for item in values),
        "categories": categories,
    }


@dataclass(frozen=True, slots=True)
class PerformanceEnvironment:
    """Safe environment/model/index identities; paths and URLs are rejected."""

    environment_id: str
    model_identity: str
    index_identity: str
    hardware_identity: str

    def __post_init__(self) -> None:
        for name in ("environment_id", "model_identity", "index_identity", "hardware_identity"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class PerformanceMeasurement:
    """One approved non-negative performance/resource measurement."""

    name: str
    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.name not in PERFORMANCE_METRIC_UNITS:
            raise ValueError("performance metric is not approved")
        expected_unit = PERFORMANCE_METRIC_UNITS[self.name]
        if self.unit != expected_unit:
            raise ValueError("performance metric unit is invalid")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("performance value must be numeric")
        numeric = float(self.value)
        if not isfinite(numeric) or numeric < 0.0:
            raise ValueError("performance value must be finite and non-negative")
        object.__setattr__(self, "value", numeric)


@dataclass(frozen=True, slots=True)
class PerformanceSample:
    """One local measurement set with explicit embedding-release evidence."""

    environment: PerformanceEnvironment
    measurements: tuple[PerformanceMeasurement, ...]
    embedding_release_verified: bool
    generation_after_embedding_release: bool

    def __post_init__(self) -> None:
        if not isinstance(self.environment, PerformanceEnvironment):
            raise ValueError("environment must be a PerformanceEnvironment")
        if (
            not isinstance(self.measurements, tuple)
            or not self.measurements
            or not all(isinstance(item, PerformanceMeasurement) for item in self.measurements)
        ):
            raise ValueError("measurements must contain at least one PerformanceMeasurement")
        names = tuple(item.name for item in self.measurements)
        if len(set(names)) != len(names):
            raise ValueError("measurement names must be unique within a sample")
        if not isinstance(self.embedding_release_verified, bool):
            raise ValueError("embedding_release_verified must be a bool")
        if not isinstance(self.generation_after_embedding_release, bool):
            raise ValueError("generation_after_embedding_release must be a bool")


def build_performance_report(samples: Sequence[PerformanceSample]) -> dict[str, object]:
    """Aggregate local measurements and require verified embedding release."""

    values = tuple(samples)
    if not values or not all(isinstance(item, PerformanceSample) for item in values):
        raise ValueError("at least one PerformanceSample is required")
    if not all(item.embedding_release_verified for item in values):
        raise ValueError("embedding release must be verified for every sample")
    if not all(item.generation_after_embedding_release for item in values):
        raise ValueError("embedding release ordering must be verified for every sample")
    first_environment = values[0].environment
    if any(item.environment != first_environment for item in values[1:]):
        raise ValueError("performance samples must share one environment identity")
    by_name: dict[str, list[float]] = {}
    units: dict[str, str] = {}
    for sample in values:
        for measurement in sample.measurements:
            by_name.setdefault(measurement.name, []).append(measurement.value)
            units[measurement.name] = measurement.unit
    metrics = [
        {
            "name": name,
            "unit": units[name],
            "sample_count": len(by_name[name]),
            "minimum": min(by_name[name]),
            "mean": round(sum(by_name[name]) / len(by_name[name]), 6),
            "maximum": max(by_name[name]),
        }
        for name in sorted(by_name)
    ]
    return {
        "sample_count": len(values),
        "environment": {
            "environment_id": first_environment.environment_id,
            "model_identity": first_environment.model_identity,
            "index_identity": first_environment.index_identity,
            "hardware_identity": first_environment.hardware_identity,
        },
        "metrics": metrics,
        "unmeasured_metrics": [name for name in PERFORMANCE_METRIC_UNITS if name not in by_name],
        "embedding_release_verified": True,
        "generation_after_embedding_release": True,
    }


def _serialize_ragas(
    result: RagasEvaluationResult, dataset: EvaluationDataset
) -> dict[str, object]:
    if result.dataset_version != dataset.dataset_version:
        raise ValueError("RAGAS dataset version does not match summary dataset")
    if result.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("RAGAS dataset fingerprint does not match summary dataset")
    return {
        "run_id": result.run_id,
        "ragas_version": result.ragas_version,
        "evaluator_model": result.evaluator_model,
        "evaluator_embeddings": result.evaluator_embeddings,
        "metric_set": list(result.metric_set),
        "metric_set_version": result.metric_set_version,
        "dataset_version": result.dataset_version,
        "dataset_fingerprint": result.dataset_fingerprint,
        "case_count": result.case_count,
        "language_counts": [
            {"language": language, "count": count} for language, count in result.language_counts
        ],
        "metrics": [{"name": item.name, "value": item.value} for item in result.metrics],
        "split_fingerprint": result.split_fingerprint,
    }


def build_evaluation_summary(
    dataset: EvaluationDataset,
    *,
    run_id: str,
    human_labels: Sequence[HumanReviewLabel] | None = None,
    safety_observations: Sequence[SafetyObservation] | None = None,
    performance_samples: Sequence[PerformanceSample] | None = None,
    ragas_result: RagasEvaluationResult | None = None,
    split: EvaluationSplit | None = None,
    retrieval_manifest: SafeRunManifest | None = None,
) -> EvaluationSummary:
    """Compose only sanitized reports while preserving dataset lineage."""

    lineage = _lineage(dataset, split)
    safe_run_id = _identifier(run_id, "run_id")
    manifest: dict[str, object] | None = None
    if retrieval_manifest is not None:
        if not isinstance(retrieval_manifest, SafeRunManifest):
            raise ValueError("retrieval_manifest must be a SafeRunManifest")
        if retrieval_manifest.dataset_fingerprint != dataset.dataset_fingerprint:
            raise ValueError("retrieval manifest does not belong to dataset")
        manifest = {
            "dataset_fingerprint": retrieval_manifest.dataset_fingerprint,
            "split_fingerprint": retrieval_manifest.split_fingerprint,
            "calibration_case_count": retrieval_manifest.calibration_case_count,
            "held_out_case_count": retrieval_manifest.held_out_case_count,
            "modes": [mode.value for mode in retrieval_manifest.modes],
            "cutoffs": list(retrieval_manifest.cutoffs),
            "ablation_chunk_targets": list(retrieval_manifest.ablation_chunk_targets),
        }
        if lineage["split_fingerprint"] is not None and (
            manifest["split_fingerprint"] != lineage["split_fingerprint"]
        ):
            raise ValueError("retrieval manifest split does not match summary split")

    safe_dataset_version = _identifier(dataset.dataset_version, "dataset_version")
    return {
        "schema_version": "phase4-evaluation-v1",
        "run_id": safe_run_id,
        "dataset_version": safe_dataset_version,
        **lineage,
        "retrieval_manifest": manifest,
        "human": None
        if human_labels is None
        else build_human_report(dataset, human_labels, split=split),
        "safety": None
        if safety_observations is None
        else build_safety_report(dataset, safety_observations, split=split),
        "performance": None
        if performance_samples is None
        else build_performance_report(performance_samples),
        "ragas": None if ragas_result is None else _serialize_ragas(ragas_result, dataset),
    }


def build_acceptance_report(
    dataset: EvaluationDataset,
    *,
    run_id: str,
    human_labels: Sequence[HumanReviewLabel] | None = None,
    safety_observations: Sequence[SafetyObservation] | None = None,
    performance_samples: Sequence[PerformanceSample] | None = None,
    ragas_result: RagasEvaluationResult | None = None,
    split: EvaluationSplit | None = None,
    retrieval_manifest: SafeRunManifest | None = None,
) -> EvaluationSummary:
    """Build the final acceptance aggregate through the existing safe schema.

    Task 26 needs an explicit acceptance-named entry point, but acceptance
    evidence uses the same lineage and allowlisted report sections as the
    deterministic evaluation summary.  Keeping this as a typed delegation
    prevents a second report schema from drifting away from the established
    privacy validation boundary.
    """

    return build_evaluation_summary(
        dataset,
        run_id=run_id,
        human_labels=human_labels,
        safety_observations=safety_observations,
        performance_samples=performance_samples,
        ragas_result=ragas_result,
        split=split,
        retrieval_manifest=retrieval_manifest,
    )


def serialize_evaluation_summary(summary: EvaluationSummary) -> str:
    """Return deterministic JSON for a validated summary."""

    if not isinstance(summary, dict) or set(summary) != SAFE_ACCEPTANCE_FIELDS:
        raise ValueError("summary contains fields outside the safe acceptance schema")
    _identifier(summary["run_id"], "run_id")
    _identifier(summary["dataset_version"], "dataset_version")
    _digest(summary["dataset_fingerprint"], "dataset_fingerprint")
    split_fingerprint = summary["split_fingerprint"]
    if split_fingerprint is not None:
        _digest(split_fingerprint, "split_fingerprint")
    retrieval_manifest = summary["retrieval_manifest"]
    _validate_retrieval_manifest(retrieval_manifest)
    _validate_nested_report(summary["human"], _HUMAN_FIELDS, "human")
    _validate_nested_report(summary["safety"], _SAFETY_FIELDS, "safety")
    _validate_nested_report(summary["performance"], _PERFORMANCE_FIELDS, "performance")
    _validate_nested_report(summary["ragas"], _RAGAS_FIELDS, "ragas")
    return json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _validate_nested_report(value: object, allowed_fields: frozenset[str], name: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != allowed_fields:
        raise ValueError(f"{name} contains fields outside the safe report schema")
    if name == "human":
        _validate_report_digest(value, "dataset_fingerprint", name)
        _validate_optional_report_digest(value, "split_fingerprint", name)
        _validate_report_identifiers(value["case_ids"], name)
        _validate_report_hashes(value["reviewer_identity_hashes"], name)
        if value["rubric_dimensions"] != list(HUMAN_RUBRIC_DIMENSIONS):
            raise ValueError(f"{name} contains an invalid rubric dimension list")
        _validate_report_number_map(value["rubric_means"], HUMAN_RUBRIC_DIMENSIONS, name)
        subgroup = value["subgroup_rubric_means"]
        if not isinstance(subgroup, dict):
            raise ValueError(f"{name} contains invalid subgroup means")
        for pair, means in subgroup.items():
            if not isinstance(pair, str) or not re.fullmatch(r"(?:ar|en)->(?:ar|en)", pair):
                raise ValueError(f"{name} contains invalid subgroup means")
            _validate_report_number_map(means, HUMAN_RUBRIC_DIMENSIONS, name)
        if not isinstance(value["sufficiency_outcomes"], dict) or set(
            value["sufficiency_outcomes"]
        ) != {
            "supported_acceptance",
            "unsupported_rejection",
            "false_acceptance",
            "false_refusal",
            "partial_expected",
            "partial_observed",
        }:
            raise ValueError(f"{name} contains invalid sufficiency outcomes")
        _validate_nonnegative_ints(value["sufficiency_outcomes"].values(), name)
        _validate_probability(value["confidence_mean"], name)
    elif name == "safety":
        _validate_report_digest(value, "dataset_fingerprint", name)
        _validate_optional_report_digest(value, "split_fingerprint", name)
        _validate_report_identifiers(value["case_ids"], name)
        categories = value["categories"]
        if not isinstance(categories, list) or len(categories) != len(SAFETY_CATEGORIES):
            raise ValueError(f"{name} contains invalid categories")
        for item, expected in zip(categories, SAFETY_CATEGORIES, strict=True):
            if (
                not isinstance(item, dict)
                or set(item)
                != {
                    "category",
                    "observed_count",
                    "pass_count",
                    "fail_count",
                }
                or item["category"] != expected
            ):
                raise ValueError(f"{name} contains invalid category data")
            _validate_nonnegative_ints(
                (item["observed_count"], item["pass_count"], item["fail_count"]), name
            )
        _validate_nonnegative_ints((value["case_count"], value["observation_count"]), name)
        if not isinstance(value["overall_pass"], bool):
            raise ValueError(f"{name} contains invalid overall result")
    elif name == "performance":
        environment = value["environment"]
        if not isinstance(environment, dict) or set(environment) != {
            "environment_id",
            "model_identity",
            "index_identity",
            "hardware_identity",
        }:
            raise ValueError(f"{name} contains invalid environment data")
        for key, item in environment.items():
            _identifier(item, f"{name}.{key}")
        metrics = value["metrics"]
        if not isinstance(metrics, list):
            raise ValueError(f"{name} contains invalid metrics")
        for metric in metrics:
            if not isinstance(metric, dict) or set(metric) != {
                "name",
                "unit",
                "sample_count",
                "minimum",
                "mean",
                "maximum",
            }:
                raise ValueError(f"{name} contains invalid metric data")
            if (
                metric["name"] not in PERFORMANCE_METRIC_UNITS
                or metric["unit"] != PERFORMANCE_METRIC_UNITS[metric["name"]]
            ):
                raise ValueError(f"{name} contains invalid metric identity")
            _validate_nonnegative_ints((metric["sample_count"],), name)
            for key in ("minimum", "mean", "maximum"):
                _validate_nonnegative_number(metric[key], name)
        if not isinstance(value["unmeasured_metrics"], list) or any(
            item not in PERFORMANCE_METRIC_UNITS for item in value["unmeasured_metrics"]
        ):
            raise ValueError(f"{name} contains invalid unmeasured metrics")
        _validate_nonnegative_ints((value["sample_count"],), name)
        if not isinstance(value["embedding_release_verified"], bool) or not isinstance(
            value["generation_after_embedding_release"], bool
        ):
            raise ValueError(f"{name} contains invalid lifecycle evidence")
    elif name == "ragas":
        for key in (
            "run_id",
            "ragas_version",
            "evaluator_model",
            "evaluator_embeddings",
            "metric_set_version",
            "dataset_version",
        ):
            _identifier(value[key], f"{name}.{key}")
        _validate_report_digest(value, "dataset_fingerprint", name)
        _validate_optional_report_digest(value, "split_fingerprint", name)


def _validate_retrieval_manifest(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _RETRIEVAL_MANIFEST_FIELDS:
        raise ValueError("retrieval_manifest contains fields outside the safe report schema")
    _validate_report_digest(value, "dataset_fingerprint", "retrieval_manifest")
    _validate_report_digest(value, "split_fingerprint", "retrieval_manifest")
    _validate_nonnegative_ints(
        (value["calibration_case_count"], value["held_out_case_count"]),
        "retrieval_manifest",
    )
    modes = value["modes"]
    if not isinstance(modes, list) or not modes:
        raise ValueError("retrieval_manifest contains invalid modes")
    for item in modes:
        _identifier(item, "retrieval_manifest.mode")
    for key in ("cutoffs", "ablation_chunk_targets"):
        items = value[key]
        if not isinstance(items, list):
            raise ValueError("retrieval_manifest contains invalid numeric lists")
        _validate_nonnegative_ints(items, "retrieval_manifest")


def _validate_report_digest(value: dict[str, object], key: str, name: str) -> None:
    try:
        _digest(value[key], f"{name}.{key}")
    except (KeyError, ValueError):
        raise ValueError(f"{name} contains an invalid digest") from None


def _validate_optional_report_digest(value: dict[str, object], key: str, name: str) -> None:
    item = value[key]
    if item is not None:
        _validate_report_digest(value, key, name)


def _validate_report_identifiers(value: object, name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{name} contains invalid identifiers")
    try:
        for item in value:
            _identifier(item, f"{name}.case_id")
    except ValueError:
        raise ValueError(f"{name} contains unsafe report values") from None


def _validate_report_hashes(value: object, name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{name} contains invalid reviewer hashes")
    try:
        for item in value:
            _digest(item, f"{name}.reviewer_identity_hash")
    except ValueError:
        raise ValueError(f"{name} contains unsafe report values") from None


def _validate_nonnegative_ints(values: object, name: str) -> None:
    if not isinstance(values, Iterable) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in values
    ):
        raise ValueError(f"{name} contains invalid counts")


def _validate_nonnegative_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} contains invalid numeric data")
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} contains invalid numeric data")


def _validate_probability(value: object, name: str) -> None:
    _validate_nonnegative_number(value, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} contains invalid probability")
    if float(value) > 1:
        raise ValueError(f"{name} contains invalid probability")


def _validate_report_number_map(
    value: object, expected_keys: Sequence[str] | None, name: str
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} contains invalid numeric map")
    if expected_keys is not None and set(value) != set(expected_keys):
        raise ValueError(f"{name} contains invalid numeric map keys")
    for item in value.values():
        _validate_nonnegative_number(item, name)


def write_evaluation_summary(
    output_root: Path,
    summary: EvaluationSummary,
    *,
    filename: str = "evaluation-summary.json",
) -> Path:
    """Write one deterministic artifact under an explicitly injected root."""

    if not isinstance(output_root, Path):
        raise ValueError("output_root must be a pathlib.Path")
    if filename != "evaluation-summary.json":
        raise ValueError("only the fixed evaluation summary filename is allowed")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / filename
    destination.write_text(serialize_evaluation_summary(summary), encoding="utf-8")
    return destination


# Descriptive aliases for callers using the longer report-builder names.
build_human_evaluation_report = build_human_report
build_safety_evaluation_report = build_safety_report
build_performance_evaluation_report = build_performance_report


__all__ = [
    "EvaluationSummary",
    "HUMAN_RUBRIC_DIMENSIONS",
    "HumanReviewLabel",
    "PERFORMANCE_METRIC_UNITS",
    "PerformanceEnvironment",
    "PerformanceMeasurement",
    "PerformanceSample",
    "SAFE_ACCEPTANCE_FIELDS",
    "SAFETY_CATEGORIES",
    "RubricScore",
    "SafetyObservation",
    "build_evaluation_summary",
    "build_acceptance_report",
    "build_human_evaluation_report",
    "build_human_report",
    "build_performance_evaluation_report",
    "build_performance_report",
    "build_safety_evaluation_report",
    "build_safety_report",
    "serialize_evaluation_summary",
    "write_evaluation_summary",
]
