"""Independent, deterministic Phase 9 evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 64:
            raise ValueError("metric name is not bounded")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.numerator, self.denominator)
        ):
            raise ValueError("metric counts must be non-negative integers")
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed denominator")

    @property
    def ratio(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0


class NliLabel(StrEnum):
    ENTAILMENT = "ENTAILMENT"
    NEUTRAL = "NEUTRAL"
    CONTRADICTION = "CONTRADICTION"


@dataclass(frozen=True, slots=True)
class NliConfusion:
    entailment: int
    neutral: int
    contradiction: int
    total: int


@dataclass(frozen=True, slots=True)
class VisualSelectionReport:
    metric: Metric
    provenance_kinds: tuple[str, ...]


def _bounded_count(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def recall_at_k(relevant: tuple[str, ...], retrieved: tuple[str, ...], *, k: int) -> Metric:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be positive")
    if len(set(relevant)) != len(relevant):
        raise ValueError("relevant IDs must be unique")
    selected = set(retrieved[:k])
    return Metric("recall_at_k", len(selected & set(relevant)), len(relevant))


def unsupported_claim_rate(*, unsupported: int, total: int) -> Metric:
    _bounded_count(unsupported, "unsupported")
    _bounded_count(total, "total")
    if unsupported > total:
        raise ValueError("unsupported claims cannot exceed total claims")
    return Metric("unsupported_claim_rate", unsupported, total)


def nli_confusion(expected: tuple[NliLabel, ...], observed: tuple[NliLabel, ...]) -> NliConfusion:
    if len(expected) != len(observed):
        raise ValueError("NLI partitions must have equal length")
    if not all(isinstance(value, NliLabel) for value in expected + observed):
        raise TypeError("NLI labels are closed")
    return NliConfusion(
        entailment=sum(a is NliLabel.ENTAILMENT and a is b for a, b in zip(expected, observed)),
        neutral=sum(a is NliLabel.NEUTRAL and a is b for a, b in zip(expected, observed)),
        contradiction=sum(
            a is NliLabel.CONTRADICTION and a is b for a, b in zip(expected, observed)
        ),
        total=len(expected),
    )


def citation_accuracy(*, correct: int, total: int) -> Metric:
    _bounded_count(correct, "correct")
    _bounded_count(total, "total")
    if correct > total:
        raise ValueError("correct citations cannot exceed total citations")
    return Metric("citation_accuracy", correct, total)


def routing_domain_report(case_ids: tuple[str, ...], *, correct: int) -> Metric:
    _bounded_count(correct, "correct")
    if correct > len(case_ids):
        raise ValueError("correct routing cases cannot exceed total cases")
    return Metric("routing_correctness", correct, len(case_ids))


def visual_selection_report(
    *, selected: int, expected: int, provenance_kinds: tuple[str, ...]
) -> VisualSelectionReport:
    _bounded_count(selected, "selected")
    _bounded_count(expected, "expected")
    if selected > expected:
        raise ValueError("selected visual elements cannot exceed expected elements")
    allowed = {"EMBEDDED_PDF", "DERIVED_VISUAL_EVIDENCE"}
    if not set(provenance_kinds) <= allowed:
        raise ValueError("visual provenance kind is not closed")
    return VisualSelectionReport(
        Metric("visual_selection_recall", selected, expected), tuple(sorted(set(provenance_kinds)))
    )


__all__ = [
    "Metric",
    "NliConfusion",
    "NliLabel",
    "VisualSelectionReport",
    "citation_accuracy",
    "nli_confusion",
    "recall_at_k",
    "routing_domain_report",
    "unsupported_claim_rate",
    "visual_selection_report",
]
