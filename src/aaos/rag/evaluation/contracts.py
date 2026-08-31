"""Immutable contracts for deterministic Phase 4 retrieval evaluation.

The evaluation package consumes sanitized manifests and ranked outputs only.
It has no repository, model, filesystem, or network side effects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import TypeAlias

from ..contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    CourseScope,
    GlobalScope,
    HistoricalScope,
    RagScope,
)
from ..retrieval import SufficiencyStatus

EvaluationExpectedStatus: TypeAlias = SufficiencyStatus

_LANGUAGES = frozenset({"en", "ar"})
_REQUIRED_CATEGORIES = frozenset(
    {
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
    }
)
_ABLATION_TOKENS = frozenset({300, 500, 700})


def _nonblank_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    normalized = value.strip()
    if len(normalized) > MAX_RAG_TEXT_CHARACTERS:
        raise ValueError(f"{name} exceeds the maximum length")
    return normalized


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _language(value: object, name: str) -> str:
    normalized = _nonblank_text(value, name).casefold()
    if normalized not in _LANGUAGES:
        raise ValueError(f"{name} must be 'en' or 'ar'")
    return normalized


class RetrievalMode(StrEnum):
    """The only retrieval baselines admitted by the evaluation protocol."""

    DENSE_ONLY = "DENSE_ONLY"
    FTS5_ONLY = "FTS5_ONLY"
    LEXICAL_ONLY = "FTS5_ONLY"
    HYBRID_RRF = "HYBRID_RRF"


@dataclass(frozen=True, slots=True)
class RelevanceJudgment:
    """A gold document-version judgment for one evaluation case."""

    document_version_id: int
    page_numbers: tuple[int, ...]
    grade: int

    def __post_init__(self) -> None:
        _positive_int(self.document_version_id, "document_version_id")
        if not isinstance(self.page_numbers, tuple) or not self.page_numbers:
            raise ValueError("page_numbers must be a non-empty tuple")
        for page_number in self.page_numbers:
            _positive_int(page_number, "page_numbers")
        if len(set(self.page_numbers)) != len(self.page_numbers):
            raise ValueError("page_numbers must be unique")
        if (
            isinstance(self.grade, bool)
            or not isinstance(self.grade, int)
            or self.grade not in {1, 2, 3}
        ):
            raise ValueError("grade must be one of 1, 2, or 3")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Sanitized gold metadata; question text is never emitted by reports."""

    case_id: str
    question: str
    query_language: str
    answer_language: str
    expected_status: SufficiencyStatus
    scope: RagScope
    relevance: tuple[RelevanceJudgment, ...]
    categories: tuple[str, ...]
    authorized_document_version_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _nonblank_text(self.case_id, "case_id"))
        object.__setattr__(self, "question", _nonblank_text(self.question, "question"))
        object.__setattr__(self, "query_language", _language(self.query_language, "query_language"))
        object.__setattr__(
            self, "answer_language", _language(self.answer_language, "answer_language")
        )
        if not isinstance(self.expected_status, SufficiencyStatus):
            raise ValueError("expected_status must be a SufficiencyStatus")
        if not isinstance(self.scope, (CourseScope, GlobalScope, HistoricalScope)):
            raise ValueError("scope must be a supported RagScope")
        if not isinstance(self.relevance, tuple) or not all(
            isinstance(item, RelevanceJudgment) for item in self.relevance
        ):
            raise ValueError("relevance must be a tuple of RelevanceJudgment")
        relevance_ids = tuple(item.document_version_id for item in self.relevance)
        if len(set(relevance_ids)) != len(relevance_ids):
            raise ValueError("relevance document-version IDs must be unique")
        if not isinstance(self.categories, tuple) or not self.categories:
            raise ValueError("categories must be a non-empty tuple")
        normalized_categories = tuple(
            _nonblank_text(item, "categories").casefold() for item in self.categories
        )
        if len(set(normalized_categories)) != len(normalized_categories):
            raise ValueError("categories must be unique")
        object.__setattr__(self, "categories", normalized_categories)
        if not isinstance(self.authorized_document_version_ids, tuple):
            raise ValueError("authorized_document_version_ids must be a tuple")
        for document_version_id in self.authorized_document_version_ids:
            _positive_int(document_version_id, "authorized_document_version_ids")
        if len(set(self.authorized_document_version_ids)) != len(
            self.authorized_document_version_ids
        ):
            raise ValueError("authorized_document_version_ids must be unique")
        authorized = set(self.authorized_document_version_ids)
        if any(item.document_version_id not in authorized for item in self.relevance):
            raise ValueError("relevance must remain inside the authorized version universe")
        if self.expected_status is SufficiencyStatus.UNSUPPORTED and self.relevance:
            raise ValueError("unsupported cases must not contain relevant versions")


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """A complete sanitized manifest used for calibration and held-out runs."""

    cases: tuple[EvaluationCase, ...]
    dataset_version: str
    split_seed: str = "phase4-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("cases must be a non-empty tuple")
        if not all(isinstance(case, EvaluationCase) for case in self.cases):
            raise ValueError("cases must contain EvaluationCase values")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case IDs must be unique")
        object.__setattr__(
            self, "dataset_version", _nonblank_text(self.dataset_version, "dataset_version")
        )
        object.__setattr__(self, "split_seed", _nonblank_text(self.split_seed, "split_seed"))

    @property
    def dataset_fingerprint(self) -> str:
        """Return a stable identity without exposing private question text."""
        records = []
        for case in sorted(self.cases, key=lambda item: item.case_id):
            records.append(
                {
                    "case_id": case.case_id,
                    "question_sha256": sha256(case.question.encode("utf-8")).hexdigest(),
                    "query_language": case.query_language,
                    "answer_language": case.answer_language,
                    "expected_status": case.expected_status.value,
                    "scope": type(case.scope).__name__,
                    "relevance": [
                        (item.document_version_id, item.page_numbers, item.grade)
                        for item in case.relevance
                    ],
                    "categories": case.categories,
                    "authorized_document_version_ids": case.authorized_document_version_ids,
                }
            )
        payload = json.dumps(
            {"dataset_version": self.dataset_version, "cases": records},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    @property
    def fingerprint(self) -> str:
        """Compatibility spelling for the sanitized dataset identity."""
        return self.dataset_fingerprint

    def validate_target_set(self) -> None:
        """Validate the approved 80–120 case calibration/held-out target."""
        if not 80 <= len(self.cases) <= 120:
            raise ValueError("target dataset must contain between 80 and 120 cases")
        if {case.query_language for case in self.cases} != _LANGUAGES or {
            case.answer_language for case in self.cases
        } != _LANGUAGES:
            raise ValueError("target dataset must cover English and Arabic queries and answers")
        if not any(case.query_language != case.answer_language for case in self.cases):
            raise ValueError("target dataset must cover cross-language cases")
        categories = {category for case in self.cases for category in case.categories}
        if _REQUIRED_CATEGORIES - categories:
            raise ValueError("target dataset is missing required categories")


@dataclass(frozen=True, slots=True)
class EvaluationSplit:
    """Stable split manifest; held-out IDs are immutable once created."""

    calibration_case_ids: tuple[str, ...]
    held_out_case_ids: tuple[str, ...]
    dataset_fingerprint: str
    seed: str
    held_out_locked: bool = True

    def __post_init__(self) -> None:
        for name in ("calibration_case_ids", "held_out_case_ids"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise ValueError(f"{name} must contain non-blank strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        if set(self.calibration_case_ids) & set(self.held_out_case_ids):
            raise ValueError("calibration and held-out IDs must be disjoint")
        fingerprint = _nonblank_text(self.dataset_fingerprint, "dataset_fingerprint").casefold()
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError("dataset_fingerprint must be a SHA-256 digest")
        object.__setattr__(self, "dataset_fingerprint", fingerprint)
        object.__setattr__(self, "seed", _nonblank_text(self.seed, "seed"))
        if not isinstance(self.held_out_locked, bool):
            raise ValueError("held_out_locked must be a bool")


@dataclass(frozen=True, slots=True)
class RetrievalRun:
    """One ranked output list for one case and one fixed retrieval mode."""

    case_id: str
    mode: RetrievalMode
    ranked_document_version_ids: tuple[int, ...]
    chunk_limit: int | None = None
    sufficiency_status: SufficiencyStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _nonblank_text(self.case_id, "case_id"))
        if not isinstance(self.mode, RetrievalMode):
            raise ValueError("mode must be a RetrievalMode")
        if not isinstance(self.ranked_document_version_ids, tuple):
            raise ValueError("ranked_document_version_ids must be a tuple")
        for document_version_id in self.ranked_document_version_ids:
            _positive_int(document_version_id, "ranked_document_version_ids")
        if self.chunk_limit is not None and self.chunk_limit not in _ABLATION_TOKENS:
            raise ValueError("chunk_limit must be one of 300, 500, or 700")
        if self.sufficiency_status is not None and not isinstance(
            self.sufficiency_status, SufficiencyStatus
        ):
            raise ValueError("sufficiency_status must be a SufficiencyStatus")


@dataclass(frozen=True, slots=True)
class SufficiencyObservation:
    case_id: str
    status: SufficiencyStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _nonblank_text(self.case_id, "case_id"))
        if not isinstance(self.status, SufficiencyStatus):
            raise ValueError("status must be a SufficiencyStatus")


@dataclass(frozen=True, slots=True)
class AblationConfig:
    chunk_target_tokens: int

    def __post_init__(self) -> None:
        if self.chunk_target_tokens not in _ABLATION_TOKENS:
            raise ValueError("chunk_target_tokens must be one of 300, 500, or 700")


@dataclass(frozen=True, slots=True)
class AblationReport:
    configurations: tuple[AblationConfig, ...]
    metrics: tuple[RetrievalMetrics, ...]


@dataclass(frozen=True, slots=True)
class MetricAtK:
    k: int
    value: float


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    mode: RetrievalMode
    evaluated_query_count: int
    zero_relevant_query_count: int
    recall_at_k: tuple[MetricAtK, ...]
    mrr: float
    ndcg_at_k: tuple[MetricAtK, ...]


@dataclass(frozen=True, slots=True)
class SufficiencyMetrics:
    evaluated_case_count: int
    supported_acceptance_count: int
    unsupported_rejection_count: int
    false_acceptance_count: int
    false_refusal_count: int
    supported_acceptance_rate: float
    unsupported_rejection_rate: float
    false_acceptance_rate: float
    false_refusal_rate: float


@dataclass(frozen=True, slots=True)
class SafeRunManifest:
    dataset_fingerprint: str
    split_fingerprint: str
    calibration_case_count: int
    held_out_case_count: int
    modes: tuple[RetrievalMode, ...]
    cutoffs: tuple[int, ...]
    ablation_chunk_targets: tuple[int, ...]


__all__ = [
    "AblationReport",
    "AblationConfig",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationExpectedStatus",
    "EvaluationSplit",
    "MetricAtK",
    "RelevanceJudgment",
    "RetrievalMetrics",
    "RetrievalMode",
    "RetrievalRun",
    "SafeRunManifest",
    "SufficiencyMetrics",
    "SufficiencyObservation",
]
