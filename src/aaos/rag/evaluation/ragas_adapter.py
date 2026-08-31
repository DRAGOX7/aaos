"""Explicit local-only adapter for supplementary RAGAS evaluation.

The evaluation dependency is intentionally kept out of the application import
graph.  A caller must inject a local evaluator (or a factory for one); this
module never discovers providers, reads credential environment variables, or
constructs a network-backed judge.  The evaluator receives only the typed,
request-local examples supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from typing import Protocol, TypeAlias

from ..contracts import MAX_RAG_TEXT_CHARACTERS
from ..evaluation.contracts import EvaluationDataset, EvaluationSplit

RAGAS_VERSION = "0.4.3"
RAGAS_METRICS = (
    "faithfulness",
    # RAGAS 0.4.3 exposes this metric as ``answer_relevancy``.
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
RAGAS_METRIC_SET_VERSION = "phase4-ragas-v1"
_LANGUAGES = frozenset({"en", "ar"})
_SAFE_IDENTIFIER_MAX = 256


def _safe_text(value: object, name: str, *, maximum: int = MAX_RAG_TEXT_CHARACTERS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    normalized = value.strip()
    if len(normalized) > maximum or any(character in normalized for character in "\r\n\x00"):
        raise ValueError(f"{name} is invalid")
    return normalized


def _safe_identifier(value: object, name: str) -> str:
    normalized = _safe_text(value, name, maximum=_SAFE_IDENTIFIER_MAX)
    if "://" in normalized:
        raise ValueError(f"{name} is invalid")
    return normalized


def _language(value: object, name: str) -> str:
    normalized = _safe_text(value, name, maximum=8).casefold()
    if normalized not in _LANGUAGES:
        raise ValueError(f"{name} must be 'en' or 'ar'")
    return normalized


class RagasEvaluationError(RuntimeError):
    """Fixed safe error for any evaluation setup or execution failure."""

    def __init__(self, code: str = "RAGAS_EVALUATION_FAILED") -> None:
        super().__init__(code)


class LocalEvaluatorUnavailableError(RagasEvaluationError):
    """Raised when no explicit local evaluator has been injected."""

    def __init__(self) -> None:
        super().__init__("LOCAL_EVALUATOR_UNAVAILABLE")


class ExternalEvaluatorRejectedError(ValueError):
    """Raised when configuration attempts to select a non-local evaluator."""

    def __init__(self) -> None:
        super().__init__("EXTERNAL_EVALUATOR_REJECTED")


@dataclass(frozen=True, slots=True)
class RagasEvaluationConfig:
    """Reproducibility identity for one explicitly local evaluation run."""

    evaluator_model: str
    evaluator_embeddings: str
    dataset_version: str
    run_id: str
    provider: str = "local"
    ragas_version: str = RAGAS_VERSION
    metric_set: tuple[str, ...] = RAGAS_METRICS
    metric_set_version: str = RAGAS_METRIC_SET_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evaluator_model", _safe_identifier(self.evaluator_model, "evaluator_model")
        )
        object.__setattr__(
            self,
            "evaluator_embeddings",
            _safe_identifier(self.evaluator_embeddings, "evaluator_embeddings"),
        )
        object.__setattr__(
            self, "dataset_version", _safe_identifier(self.dataset_version, "dataset_version")
        )
        object.__setattr__(self, "run_id", _safe_identifier(self.run_id, "run_id"))
        provider = _safe_identifier(self.provider, "provider").casefold()
        if provider != "local":
            raise ExternalEvaluatorRejectedError()
        object.__setattr__(self, "provider", provider)
        ragas_version = _safe_identifier(self.ragas_version, "ragas_version")
        if ragas_version != RAGAS_VERSION:
            raise ValueError("ragas_version does not match the pinned dependency")
        object.__setattr__(self, "ragas_version", ragas_version)
        if not isinstance(self.metric_set, tuple) or self.metric_set != RAGAS_METRICS:
            raise ValueError("metric_set must be the approved RAGAS metric set")
        object.__setattr__(self, "metric_set", tuple(self.metric_set))
        object.__setattr__(
            self,
            "metric_set_version",
            _safe_identifier(self.metric_set_version, "metric_set_version"),
        )


@dataclass(frozen=True, slots=True)
class RagasEvaluationExample:
    """One bounded evaluation input; text is data and is never reported."""

    case_id: str
    question: str = field(repr=False)
    answer: str = field(repr=False)
    contexts: tuple[str, ...] = field(repr=False)
    reference: str | None = field(default=None, repr=False)
    query_language: str = "en"
    answer_language: str = "en"
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _safe_identifier(self.case_id, "case_id"))
        object.__setattr__(self, "question", _safe_text(self.question, "question"))
        object.__setattr__(self, "answer", _safe_text(self.answer, "answer"))
        if not isinstance(self.contexts, tuple) or not self.contexts:
            raise ValueError("contexts must be a non-empty tuple")
        normalized_contexts = tuple(_safe_text(context, "contexts") for context in self.contexts)
        object.__setattr__(self, "contexts", normalized_contexts)
        if self.reference is not None:
            object.__setattr__(self, "reference", _safe_text(self.reference, "reference"))
        object.__setattr__(self, "query_language", _language(self.query_language, "query_language"))
        object.__setattr__(
            self, "answer_language", _language(self.answer_language, "answer_language")
        )
        if not isinstance(self.evidence_ids, tuple):
            raise ValueError("evidence_ids must be a tuple")
        normalized_ids = tuple(_safe_identifier(item, "evidence_ids") for item in self.evidence_ids)
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("evidence_ids must be unique")
        object.__setattr__(self, "evidence_ids", normalized_ids)


@dataclass(frozen=True, slots=True)
class RagasEvaluationRun:
    """Dataset/examples binding passed to one local evaluator invocation."""

    dataset: EvaluationDataset = field(repr=False)
    examples: tuple[RagasEvaluationExample, ...] = field(repr=False)
    split: EvaluationSplit | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, EvaluationDataset):
            raise ValueError("dataset must be an EvaluationDataset")
        if not isinstance(self.examples, tuple) or not self.examples:
            raise ValueError("examples must be a non-empty tuple")
        if not all(isinstance(example, RagasEvaluationExample) for example in self.examples):
            raise ValueError("examples must contain RagasEvaluationExample values")
        case_ids = tuple(example.case_id for example in self.examples)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("example case IDs must be unique")
        if self.split is not None and not isinstance(self.split, EvaluationSplit):
            raise ValueError("split must be an EvaluationSplit")


@dataclass(frozen=True, slots=True)
class RagasMetricValue:
    """One bounded aggregate score for an approved metric."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in RAGAS_METRICS:
            raise ValueError("metric is not approved")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("metric value must be numeric")
        numeric = float(self.value)
        if not isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError("metric value must be finite and between zero and one")
        object.__setattr__(self, "value", numeric)


@dataclass(frozen=True, slots=True)
class RagasEvaluationResult:
    """Safe aggregate result with no question, answer, context, or raw output."""

    run_id: str
    ragas_version: str
    evaluator_model: str
    evaluator_embeddings: str
    metric_set: tuple[str, ...]
    metric_set_version: str
    dataset_version: str
    dataset_fingerprint: str
    case_count: int
    language_counts: tuple[tuple[str, int], ...]
    metrics: tuple[RagasMetricValue, ...]
    split_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _safe_identifier(self.run_id, "run_id"))
        object.__setattr__(
            self, "evaluator_model", _safe_identifier(self.evaluator_model, "evaluator_model")
        )
        object.__setattr__(
            self,
            "evaluator_embeddings",
            _safe_identifier(self.evaluator_embeddings, "evaluator_embeddings"),
        )
        object.__setattr__(
            self, "dataset_version", _safe_identifier(self.dataset_version, "dataset_version")
        )
        if self.ragas_version != RAGAS_VERSION:
            raise ValueError("ragas_version does not match the pinned dependency")
        if not isinstance(self.metric_set, tuple) or self.metric_set != RAGAS_METRICS:
            raise ValueError("metric_set must be the approved RAGAS metric set")
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(item, RagasMetricValue) for item in self.metrics
        ):
            raise ValueError("metrics must contain the approved metric set")
        if tuple(item.name for item in self.metrics) != RAGAS_METRICS:
            raise ValueError("metrics must contain RagasMetricValue values")
        if (
            isinstance(self.case_count, bool)
            or not isinstance(self.case_count, int)
            or self.case_count <= 0
        ):
            raise ValueError("case_count must be positive")
        if (
            not isinstance(self.language_counts, tuple)
            or len(self.language_counts) != 2
            or any(not isinstance(item, tuple) or len(item) != 2 for item in self.language_counts)
            or self.language_counts[0][0] != "ar"
            or self.language_counts[1][0] != "en"
        ):
            raise ValueError("language_counts must be deterministic")
        if any(
            not isinstance(language, str)
            or language not in _LANGUAGES
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for language, count in self.language_counts
        ):
            raise ValueError("language_counts are invalid")
        if sum(count for _, count in self.language_counts) != self.case_count:
            raise ValueError("language_counts must sum to case_count")
        if (
            not isinstance(self.dataset_fingerprint, str)
            or len(self.dataset_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.dataset_fingerprint.casefold()
            )
        ):
            raise ValueError("dataset_fingerprint must be a SHA-256 digest")
        if self.split_fingerprint is not None and (
            not isinstance(self.split_fingerprint, str)
            or len(self.split_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.split_fingerprint.casefold()
            )
        ):
            raise ValueError("split_fingerprint must be a SHA-256 digest")

    @property
    def metric_values(self) -> tuple[tuple[str, float], ...]:
        """Return metric scores in the approved deterministic order."""
        return tuple((item.name, item.value) for item in self.metrics)


class LocalRagasEvaluator(Protocol):
    """Minimal injected local evaluator boundary used by the adapter."""

    def evaluate(
        self,
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
    ) -> Mapping[str, object]: ...


LocalEvaluatorFactory: TypeAlias = Callable[[], LocalRagasEvaluator]


class RagasAdapter:
    """Run exactly one explicit local evaluator over one bounded evaluation run."""

    def __init__(
        self,
        *,
        config: RagasEvaluationConfig,
        evaluator: LocalRagasEvaluator | None = None,
        local_evaluator_factory: LocalEvaluatorFactory | None = None,
        evaluator_factory: LocalEvaluatorFactory | None = None,
    ) -> None:
        if not isinstance(config, RagasEvaluationConfig):
            raise ValueError("config must be a RagasEvaluationConfig")
        if local_evaluator_factory is not None and evaluator_factory is not None:
            raise ValueError("only one evaluator factory may be supplied")
        if evaluator is not None and (
            local_evaluator_factory is not None or evaluator_factory is not None
        ):
            raise ValueError("provide an evaluator or factory, not both")
        self._config = config
        self._evaluator = evaluator
        self._evaluator_factory = local_evaluator_factory or evaluator_factory

    @property
    def config(self) -> RagasEvaluationConfig:
        return self._config

    def evaluate(
        self,
        run_or_dataset: RagasEvaluationRun | EvaluationDataset,
        examples: Sequence[RagasEvaluationExample] | None = None,
        *,
        split: EvaluationSplit | None = None,
    ) -> RagasEvaluationResult:
        run = self._coerce_run(run_or_dataset, examples, split)
        self._validate_run(run)
        evaluator = self._resolve_evaluator()
        try:
            values = evaluator.evaluate(run.examples, self._config.metric_set)
        except RagasEvaluationError:
            raise
        except Exception:
            raise RagasEvaluationError() from None
        return self._build_result(run, values)

    def _coerce_run(
        self,
        run_or_dataset: RagasEvaluationRun | EvaluationDataset,
        examples: Sequence[RagasEvaluationExample] | None,
        split: EvaluationSplit | None,
    ) -> RagasEvaluationRun:
        if isinstance(run_or_dataset, RagasEvaluationRun):
            if examples is not None or split is not None:
                raise RagasEvaluationError("INVALID_EVALUATION_INPUT")
            return run_or_dataset
        if isinstance(run_or_dataset, EvaluationDataset) and examples is not None:
            try:
                return RagasEvaluationRun(
                    dataset=run_or_dataset,
                    examples=tuple(examples),
                    split=split,
                )
            except (TypeError, ValueError):
                raise RagasEvaluationError("INVALID_EVALUATION_INPUT") from None
        raise RagasEvaluationError("INVALID_EVALUATION_INPUT")

    def _resolve_evaluator(self) -> LocalRagasEvaluator:
        if self._evaluator is not None:
            return self._evaluator
        if self._evaluator_factory is None:
            raise LocalEvaluatorUnavailableError()
        try:
            evaluator = self._evaluator_factory()
        except Exception:
            raise RagasEvaluationError("LOCAL_EVALUATOR_UNAVAILABLE") from None
        if evaluator is None or not callable(getattr(evaluator, "evaluate", None)):
            raise RagasEvaluationError("LOCAL_EVALUATOR_UNAVAILABLE")
        return evaluator

    def _validate_run(self, run: RagasEvaluationRun) -> None:
        if self._config.dataset_version != run.dataset.dataset_version:
            raise RagasEvaluationError("DATASET_VERSION_MISMATCH")
        dataset_cases = {case.case_id: case for case in run.dataset.cases}
        for example in run.examples:
            case = dataset_cases.get(example.case_id)
            if case is None:
                raise RagasEvaluationError("UNKNOWN_CASE")
            if (
                case.query_language != example.query_language
                or case.answer_language != example.answer_language
            ):
                raise RagasEvaluationError("LANGUAGE_MISMATCH")
        if run.split is not None:
            if run.split.dataset_fingerprint != run.dataset.dataset_fingerprint:
                raise RagasEvaluationError("SPLIT_FINGERPRINT_MISMATCH")
            if not run.split.held_out_locked:
                raise RagasEvaluationError("SPLIT_NOT_LOCKED")
            split_case_ids = set(run.split.calibration_case_ids) | set(run.split.held_out_case_ids)
            if any(example.case_id not in split_case_ids for example in run.examples):
                raise RagasEvaluationError("SPLIT_CASE_MISMATCH")

    def _build_result(
        self,
        run: RagasEvaluationRun,
        values: Mapping[str, object],
    ) -> RagasEvaluationResult:
        if not isinstance(values, Mapping):
            raise RagasEvaluationError()
        if set(values) != set(self._config.metric_set):
            raise RagasEvaluationError("INVALID_METRIC_OUTPUT")
        try:
            metric_values: list[RagasMetricValue] = []
            for name in self._config.metric_set:
                value = values[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("metric value must be numeric")
                metric_values.append(RagasMetricValue(name, float(value)))
            metrics = tuple(metric_values)
        except (TypeError, ValueError, KeyError):
            raise RagasEvaluationError("INVALID_METRIC_OUTPUT") from None
        language_counts = tuple(
            (language, sum(example.query_language == language for example in run.examples))
            for language in sorted(_LANGUAGES)
        )
        return RagasEvaluationResult(
            run_id=self._config.run_id,
            ragas_version=self._config.ragas_version,
            evaluator_model=self._config.evaluator_model,
            evaluator_embeddings=self._config.evaluator_embeddings,
            metric_set=self._config.metric_set,
            metric_set_version=self._config.metric_set_version,
            dataset_version=run.dataset.dataset_version,
            dataset_fingerprint=run.dataset.dataset_fingerprint,
            case_count=len(run.examples),
            language_counts=language_counts,
            metrics=metrics,
            split_fingerprint=None if run.split is None else run.split.dataset_fingerprint,
        )


LocalOnlyRagasAdapter = RagasAdapter


def create_local_ragas_adapter(
    config: RagasEvaluationConfig,
    evaluator_factory: LocalEvaluatorFactory,
) -> RagasAdapter:
    """Construct an adapter only from an explicitly supplied local factory."""
    return RagasAdapter(config=config, local_evaluator_factory=evaluator_factory)


__all__ = [
    "ExternalEvaluatorRejectedError",
    "LocalEvaluatorFactory",
    "LocalEvaluatorUnavailableError",
    "LocalOnlyRagasAdapter",
    "LocalRagasEvaluator",
    "RAGAS_METRICS",
    "RAGAS_METRIC_SET_VERSION",
    "RAGAS_VERSION",
    "RagasAdapter",
    "RagasEvaluationConfig",
    "RagasEvaluationError",
    "RagasEvaluationExample",
    "RagasEvaluationResult",
    "RagasEvaluationRun",
    "RagasMetricValue",
    "create_local_ragas_adapter",
]
