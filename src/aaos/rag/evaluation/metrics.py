"""Pure deterministic retrieval metrics and evaluation aggregation."""

from __future__ import annotations

import json
from collections.abc import Collection, Hashable, Mapping, Sequence
from hashlib import sha256
from math import isfinite, log2
from typing import Iterable, TypeVar

from ..retrieval import SufficiencyStatus
from .contracts import (
    AblationConfig,
    AblationReport,
    EvaluationCase,
    EvaluationDataset,
    EvaluationSplit,
    MetricAtK,
    RetrievalMetrics,
    RetrievalMode,
    RetrievalRun,
    SafeRunManifest,
    SufficiencyMetrics,
    SufficiencyObservation,
)

DEFAULT_CUTOFFS: tuple[int, ...] = (1, 3, 5, 10)
IdentifierT = TypeVar("IdentifierT", bound=Hashable)


def _validate_cutoff(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be a positive integer")


def _dedupe(values: Iterable[IdentifierT]) -> tuple[IdentifierT, ...]:
    seen: set[IdentifierT] = set()
    result: list[IdentifierT] = []
    for value in values:
        try:
            if value in seen:
                continue
            seen.add(value)
        except TypeError as exc:
            raise ValueError("ranked identifiers must be hashable") from exc
        result.append(value)
    return tuple(result)


def compute_recall(
    ranked: Sequence[IdentifierT],
    relevant: Collection[IdentifierT],
    *,
    k: int,
) -> float:
    """Compute set recall at *k*, deduplicating a rank list first."""
    _validate_cutoff(k)
    try:
        relevant_set = set(relevant)
    except TypeError as exc:
        raise ValueError("relevant identifiers must be hashable") from exc
    if not relevant_set:
        return 0.0
    retrieved = set(_dedupe(ranked)[:k])
    return len(retrieved & relevant_set) / len(relevant_set)


def compute_mrr(ranked: Sequence[IdentifierT], relevant: Collection[IdentifierT]) -> float:
    """Compute reciprocal rank of the first unique relevant result."""
    try:
        relevant_set = set(relevant)
    except TypeError as exc:
        raise ValueError("relevant identifiers must be hashable") from exc
    if not relevant_set:
        return 0.0
    for rank, identifier in enumerate(_dedupe(ranked), start=1):
        if identifier in relevant_set:
            return 1.0 / rank
    return 0.0


def compute_ndcg(
    ranked: Sequence[IdentifierT],
    relevance: Mapping[IdentifierT, float],
    *,
    k: int,
) -> float:
    """Compute graded nDCG at *k* using a deterministic ideal ordering."""
    _validate_cutoff(k)
    if not relevance:
        return 0.0
    if any(not isfinite(value) or value < 0 for value in relevance.values()):
        raise ValueError("relevance grades must be finite and non-negative")
    unique_ranked = _dedupe(ranked)[:k]

    def gain(value: float) -> float:
        return float((2.0**value) - 1.0)

    dcg = sum(
        gain(relevance[identifier]) / log2(rank + 2)
        for rank, identifier in enumerate(unique_ranked)
        if identifier in relevance
    )
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum(gain(value) / log2(rank + 2) for rank, value in enumerate(ideal_grades))
    return float(dcg / ideal) if ideal else 0.0


def _case_map(dataset: EvaluationDataset) -> dict[str, EvaluationCase]:
    return {case.case_id: case for case in dataset.cases}


def _validate_runs(
    dataset: EvaluationDataset,
    runs: Sequence[RetrievalRun],
) -> tuple[RetrievalMode, dict[str, RetrievalRun]]:
    cases = _case_map(dataset)
    if not runs:
        raise ValueError("at least one retrieval run is required")
    modes = {run.mode for run in runs}
    if len(modes) != 1:
        raise ValueError("compute_metrics requires one retrieval mode")
    by_case: dict[str, RetrievalRun] = {}
    for run in runs:
        case = cases.get(run.case_id)
        if case is None:
            raise ValueError("retrieval run references an unknown case")
        if run.case_id in by_case:
            raise ValueError("duplicate retrieval run for case")
        authorized = set(case.authorized_document_version_ids)
        if any(identifier not in authorized for identifier in run.ranked_document_version_ids):
            raise ValueError("retrieval output escaped the case authorization universe")
        by_case[run.case_id] = run
    if set(by_case) != set(cases):
        raise ValueError("one retrieval run is required for every dataset case")
    return next(iter(modes)), by_case


def compute_metrics(
    dataset: EvaluationDataset,
    runs: Sequence[RetrievalRun],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> RetrievalMetrics:
    """Aggregate Recall, MRR, and nDCG without combining raw scores."""
    if not isinstance(dataset, EvaluationDataset):
        raise ValueError("dataset must be an EvaluationDataset")
    for cutoff in cutoffs:
        _validate_cutoff(cutoff)
    mode, by_case = _validate_runs(dataset, runs)
    eligible = []
    zero_relevant = 0
    for case in dataset.cases:
        if not case.relevance:
            zero_relevant += 1
            continue
        eligible.append(case)
    recall = tuple(
        MetricAtK(
            cutoff,
            (
                sum(
                    compute_recall(
                        by_case[case.case_id].ranked_document_version_ids,
                        tuple(item.document_version_id for item in case.relevance),
                        k=cutoff,
                    )
                    for case in eligible
                )
                / len(eligible)
                if eligible
                else 0.0
            ),
        )
        for cutoff in cutoffs
    )
    mrr = (
        sum(
            compute_mrr(
                by_case[case.case_id].ranked_document_version_ids,
                tuple(item.document_version_id for item in case.relevance),
            )
            for case in eligible
        )
        / len(eligible)
        if eligible
        else 0.0
    )
    ndcg = tuple(
        MetricAtK(
            cutoff,
            (
                sum(
                    compute_ndcg(
                        by_case[case.case_id].ranked_document_version_ids,
                        {item.document_version_id: float(item.grade) for item in case.relevance},
                        k=cutoff,
                    )
                    for case in eligible
                )
                / len(eligible)
                if eligible
                else 0.0
            ),
        )
        for cutoff in cutoffs
    )
    return RetrievalMetrics(mode, len(eligible), zero_relevant, recall, mrr, ndcg)


def evaluate_retrieval(
    dataset: EvaluationDataset,
    runs: Sequence[RetrievalRun],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> tuple[RetrievalMetrics, ...]:
    """Evaluate exactly dense-only, FTS5-only, and hybrid RRF runs."""
    if {run.mode for run in runs} != set(RetrievalMode):
        raise ValueError("all three approved retrieval modes are required")
    by_mode: dict[RetrievalMode, list[RetrievalRun]] = {mode: [] for mode in RetrievalMode}
    for run in runs:
        by_mode[run.mode].append(run)
    return tuple(compute_metrics(dataset, by_mode[mode], cutoffs=cutoffs) for mode in RetrievalMode)


def compute_subgroup_metrics(
    dataset: EvaluationDataset,
    runs: Sequence[RetrievalRun],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> dict[str, RetrievalMetrics]:
    """Return deterministic per-language/category metric reports."""
    mode = next(iter({run.mode for run in runs}), None)
    if mode is None:
        raise ValueError("at least one retrieval run is required")
    groups: dict[str, tuple[EvaluationCase, ...]] = {}
    groups["query:en"] = tuple(case for case in dataset.cases if case.query_language == "en")
    groups["query:ar"] = tuple(case for case in dataset.cases if case.query_language == "ar")
    groups["answer:en"] = tuple(case for case in dataset.cases if case.answer_language == "en")
    groups["answer:ar"] = tuple(case for case in dataset.cases if case.answer_language == "ar")
    for category in sorted({item for case in dataset.cases for item in case.categories}):
        groups[f"category:{category}"] = tuple(
            case for case in dataset.cases if category in case.categories
        )
    run_by_case = {run.case_id: run for run in runs}
    result: dict[str, RetrievalMetrics] = {}
    for key, selected in groups.items():
        if not selected:
            continue
        subset = EvaluationDataset(tuple(selected), dataset.dataset_version, dataset.split_seed)
        subset_runs = tuple(run_by_case[case.case_id] for case in selected)
        result[key] = compute_metrics(subset, subset_runs, cutoffs=cutoffs)
    return result


def compute_sufficiency_metrics(
    dataset: EvaluationDataset,
    observations: Sequence[SufficiencyObservation],
) -> SufficiencyMetrics:
    """Compare observed deterministic sufficiency labels with gold labels."""
    expected = {case.case_id: case.expected_status for case in dataset.cases}
    observed: dict[str, SufficiencyStatus] = {}
    for item in observations:
        if item.case_id not in expected or item.case_id in observed:
            raise ValueError("sufficiency observations must match cases exactly once")
        observed[item.case_id] = item.status
    if set(observed) != set(expected):
        raise ValueError("sufficiency observations must cover the dataset")
    supported_total = sum(status is SufficiencyStatus.SUPPORTED for status in expected.values())
    unsupported_total = sum(status is SufficiencyStatus.UNSUPPORTED for status in expected.values())
    false_acceptance = sum(
        expected[case_id] is SufficiencyStatus.UNSUPPORTED
        and status is not SufficiencyStatus.UNSUPPORTED
        for case_id, status in observed.items()
    )
    false_refusal = sum(
        expected[case_id] is SufficiencyStatus.SUPPORTED and status is SufficiencyStatus.UNSUPPORTED
        for case_id, status in observed.items()
    )
    supported_acceptance = sum(
        expected[case_id] is SufficiencyStatus.SUPPORTED and status is SufficiencyStatus.SUPPORTED
        for case_id, status in observed.items()
    )
    unsupported_rejection = sum(
        expected[case_id] is SufficiencyStatus.UNSUPPORTED
        and status is SufficiencyStatus.UNSUPPORTED
        for case_id, status in observed.items()
    )
    return SufficiencyMetrics(
        evaluated_case_count=len(expected),
        supported_acceptance_count=supported_acceptance,
        unsupported_rejection_count=unsupported_rejection,
        false_acceptance_count=false_acceptance,
        false_refusal_count=false_refusal,
        supported_acceptance_rate=supported_acceptance / supported_total
        if supported_total
        else 0.0,
        unsupported_rejection_rate=unsupported_rejection / unsupported_total
        if unsupported_total
        else 0.0,
        false_acceptance_rate=false_acceptance / unsupported_total if unsupported_total else 0.0,
        false_refusal_rate=false_refusal / supported_total if supported_total else 0.0,
    )


def deterministic_split(
    dataset: EvaluationDataset,
    *,
    seed: str | None = None,
    calibration_fraction: float = 0.7,
) -> EvaluationSplit:
    """Create a stable, disjoint split from IDs, never input order."""
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError("calibration_fraction must be between zero and one")
    split_seed = dataset.split_seed if seed is None else seed
    if not isinstance(split_seed, str) or not split_seed.strip():
        raise ValueError("seed must be non-blank")
    ordered = sorted(
        (case.case_id for case in dataset.cases),
        key=lambda case_id: sha256(f"{split_seed}\0{case_id}".encode("utf-8")).hexdigest(),
    )
    calibration_count = round(len(ordered) * calibration_fraction)
    calibration_count = min(max(calibration_count, 1), len(ordered) - 1)
    return EvaluationSplit(
        calibration_case_ids=tuple(sorted(ordered[:calibration_count])),
        held_out_case_ids=tuple(sorted(ordered[calibration_count:])),
        dataset_fingerprint=dataset.dataset_fingerprint,
        seed=split_seed,
    )


def default_ablation_configs() -> tuple[AblationConfig, ...]:
    """Return the only approved chunk-target ablation values."""
    return tuple(AblationConfig(value) for value in (300, 500, 700))


def evaluate_ablation(
    dataset: EvaluationDataset,
    runs_by_chunk_target: Mapping[int, Sequence[RetrievalRun]],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> AblationReport:
    """Evaluate exactly the approved 300/500/700 chunk-target configurations."""
    expected = default_ablation_configs()
    if set(runs_by_chunk_target) != {config.chunk_target_tokens for config in expected}:
        raise ValueError("ablation runs must contain exactly the approved chunk targets")
    metrics: list[RetrievalMetrics] = []
    for config in expected:
        runs = tuple(runs_by_chunk_target[config.chunk_target_tokens])
        if any(run.chunk_limit != config.chunk_target_tokens for run in runs):
            raise ValueError("ablation run chunk target does not match its configuration")
        metrics.append(compute_metrics(dataset, runs, cutoffs=cutoffs))
    return AblationReport(expected, tuple(metrics))


def build_safe_run_manifest(
    dataset: EvaluationDataset,
    split: EvaluationSplit,
    modes: Sequence[RetrievalMode],
    *,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> SafeRunManifest:
    """Build an aggregate-only manifest without questions or retrieved text."""
    if split.dataset_fingerprint != dataset.dataset_fingerprint:
        raise ValueError("split does not belong to dataset")
    if not split.held_out_locked:
        raise ValueError("held-out split must be locked")
    normalized_modes = tuple(dict.fromkeys(modes))
    if not normalized_modes or not all(
        isinstance(mode, RetrievalMode) for mode in normalized_modes
    ):
        raise ValueError("modes must contain RetrievalMode values")
    for cutoff in cutoffs:
        _validate_cutoff(cutoff)
    split_payload = json.dumps(
        {
            "dataset": split.dataset_fingerprint,
            "seed": split.seed,
            "calibration": split.calibration_case_ids,
            "held_out": split.held_out_case_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    split_fingerprint = sha256(split_payload).hexdigest()
    return SafeRunManifest(
        dataset_fingerprint=dataset.dataset_fingerprint,
        split_fingerprint=split_fingerprint,
        calibration_case_count=len(split.calibration_case_ids),
        held_out_case_count=len(split.held_out_case_ids),
        modes=normalized_modes,
        cutoffs=tuple(cutoffs),
        ablation_chunk_targets=tuple(
            config.chunk_target_tokens for config in default_ablation_configs()
        ),
    )


# Descriptive aliases for callers that prefer metric-style names.
recall_at_k = compute_recall
mrr = compute_mrr
ndcg_at_k = compute_ndcg
split_dataset = deterministic_split


__all__ = [
    "DEFAULT_CUTOFFS",
    "build_safe_run_manifest",
    "compute_metrics",
    "compute_mrr",
    "compute_ndcg",
    "compute_recall",
    "compute_subgroup_metrics",
    "compute_sufficiency_metrics",
    "default_ablation_configs",
    "deterministic_split",
    "evaluate_ablation",
    "evaluate_retrieval",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
    "split_dataset",
]
