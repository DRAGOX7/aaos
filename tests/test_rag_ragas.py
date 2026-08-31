"""Task 23 local-only RAGAS adapter tests."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Mapping

import pytest

from aaos.rag.contracts import GlobalScope
from aaos.rag.evaluation.contracts import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationSplit,
    RelevanceJudgment,
)
from aaos.rag.evaluation.metrics import deterministic_split
from aaos.rag.evaluation.ragas_adapter import (
    RAGAS_METRICS,
    RAGAS_VERSION,
    ExternalEvaluatorRejectedError,
    LocalEvaluatorUnavailableError,
    RagasAdapter,
    RagasEvaluationConfig,
    RagasEvaluationError,
    RagasEvaluationExample,
    RagasEvaluationResult,
    RagasEvaluationRun,
)
from aaos.rag.retrieval import SufficiencyStatus


def _dataset() -> EvaluationDataset:
    cases = tuple(
        EvaluationCase(
            case_id=f"case-{language}",
            question=f"private question {language}",
            query_language=language,
            answer_language=language,
            expected_status=SufficiencyStatus.SUPPORTED,
            scope=GlobalScope(),
            relevance=(RelevanceJudgment(101 + index, (1,), 3),),
            categories=("semantic", "current"),
            authorized_document_version_ids=(101 + index,),
        )
        for index, language in enumerate(("en", "ar"))
    )
    return EvaluationDataset(cases=cases, dataset_version="gold-v1")


def _examples() -> tuple[RagasEvaluationExample, ...]:
    return (
        RagasEvaluationExample(
            case_id="case-en",
            question="private question en",
            answer="private answer en",
            contexts=("untrusted context en",),
            reference="private reference en",
            query_language="en",
            answer_language="en",
            evidence_ids=("evidence-1",),
        ),
        RagasEvaluationExample(
            case_id="case-ar",
            question="سؤال خاص",
            answer="إجابة خاصة",
            contexts=("سياق غير موثوق",),
            reference="مرجع خاص",
            query_language="ar",
            answer_language="ar",
            evidence_ids=("evidence-2",),
        ),
    )


def _config(*, provider: str = "local") -> RagasEvaluationConfig:
    return RagasEvaluationConfig(
        evaluator_model="local-judge-v1",
        evaluator_embeddings="local-embeddings-v1",
        dataset_version="gold-v1",
        run_id="run-001",
        provider=provider,
    )


def _run(*, split: EvaluationSplit | None = None) -> RagasEvaluationRun:
    dataset = _dataset()
    return RagasEvaluationRun(dataset=dataset, examples=_examples(), split=split)


@dataclass
class _FakeEvaluator:
    values: Mapping[str, float]
    calls: int = 0
    received_metrics: tuple[str, ...] = ()
    received_examples: tuple[RagasEvaluationExample, ...] = ()

    def evaluate(
        self,
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
    ) -> Mapping[str, float]:
        self.calls += 1
        self.received_examples = examples
        self.received_metrics = metrics
        return self.values


def _values() -> dict[str, float]:
    return {metric: 0.75 for metric in RAGAS_METRICS}


def test_local_evaluator_is_required_and_no_provider_is_discovered() -> None:
    adapter = RagasAdapter(config=_config())
    with pytest.raises(LocalEvaluatorUnavailableError):
        adapter.evaluate(_run())


def test_local_fake_evaluator_returns_reproducible_safe_result() -> None:
    assert RAGAS_METRICS == (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    )
    fake = _FakeEvaluator(_values())
    result = RagasAdapter(config=_config(), evaluator=fake).evaluate(_run())

    assert isinstance(result, RagasEvaluationResult)
    assert result.ragas_version == RAGAS_VERSION == "0.4.3"
    assert result.evaluator_model == "local-judge-v1"
    assert result.evaluator_embeddings == "local-embeddings-v1"
    assert result.metric_set == RAGAS_METRICS
    assert result.dataset_version == "gold-v1"
    assert len(result.dataset_fingerprint) == 64
    assert result.run_id == "run-001"
    assert result.case_count == 2
    assert result.language_counts == (("ar", 1), ("en", 1))
    assert tuple(item.name for item in result.metrics) == RAGAS_METRICS
    assert tuple(item.value for item in result.metrics) == (0.75,) * 4
    assert fake.calls == 1
    assert fake.received_metrics == RAGAS_METRICS
    assert fake.received_examples == _examples()


def test_explicit_local_factory_is_created_once() -> None:
    fake = _FakeEvaluator(_values())
    calls = 0

    def factory() -> _FakeEvaluator:
        nonlocal calls
        calls += 1
        return fake

    result = RagasAdapter(config=_config(), local_evaluator_factory=factory).evaluate(_run())
    assert result.case_count == 2
    assert calls == 1
    assert fake.calls == 1


def test_external_provider_is_rejected_and_environment_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ExternalEvaluatorRejectedError):
        _config(provider="openai")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    with pytest.raises(LocalEvaluatorUnavailableError):
        RagasAdapter(config=_config()).evaluate(_run())


@pytest.mark.parametrize(
    "values",
    (
        {"faithfulness": 0.5},
        {**_values(), "unexpected": 0.5},
        {metric: 1.1 for metric in RAGAS_METRICS},
        {metric: math.nan for metric in RAGAS_METRICS},
    ),
)
def test_malformed_metric_output_fails_closed(values: Mapping[str, float]) -> None:
    with pytest.raises(RagasEvaluationError):
        RagasAdapter(config=_config(), evaluator=_FakeEvaluator(values)).evaluate(_run())


def test_evaluator_failure_is_sanitized() -> None:
    class FailingEvaluator:
        def evaluate(
            self,
            examples: tuple[RagasEvaluationExample, ...],
            metrics: tuple[str, ...],
        ) -> Mapping[str, float]:
            raise RuntimeError("private token and answer text")

    with pytest.raises(RagasEvaluationError) as error:
        RagasAdapter(config=_config(), evaluator=FailingEvaluator()).evaluate(_run())
    assert str(error.value) == "RAGAS_EVALUATION_FAILED"
    assert "private" not in str(error.value)


def test_dataset_version_and_split_fingerprint_are_bound() -> None:
    dataset = _dataset()
    split = deterministic_split(dataset, seed="test")
    result = RagasAdapter(config=_config(), evaluator=_FakeEvaluator(_values())).evaluate(
        _run(split=split)
    )
    assert result.split_fingerprint == split.dataset_fingerprint

    mismatched = EvaluationSplit(
        calibration_case_ids=split.calibration_case_ids,
        held_out_case_ids=split.held_out_case_ids,
        dataset_fingerprint="0" * 64,
        seed=split.seed,
    )
    with pytest.raises(RagasEvaluationError):
        RagasAdapter(config=_config(), evaluator=_FakeEvaluator(_values())).evaluate(
            _run(split=mismatched)
        )


def test_unknown_or_duplicate_cases_and_invalid_evidence_fail_closed() -> None:
    dataset = _dataset()
    run = RagasEvaluationRun(dataset=dataset, examples=_examples())
    with pytest.raises(ValueError):
        RagasEvaluationExample(
            case_id="case-en",
            question="private question en",
            answer="private answer en",
            contexts=("context",),
            reference="reference",
            query_language="en",
            answer_language="en",
            evidence_ids=("evidence-1", "evidence-1"),
        )

    with pytest.raises(ValueError):
        RagasEvaluationRun(dataset=dataset, examples=(run.examples[0], run.examples[0]))

    unknown = RagasEvaluationExample(
        case_id="not-in-dataset",
        question="private question",
        answer="private answer",
        contexts=("context",),
        reference="reference",
        query_language="en",
        answer_language="en",
    )
    with pytest.raises(RagasEvaluationError):
        RagasAdapter(config=_config(), evaluator=_FakeEvaluator(_values())).evaluate(
            dataset, (unknown,)
        )


def test_private_text_is_not_in_safe_result_repr() -> None:
    result = RagasAdapter(config=_config(), evaluator=_FakeEvaluator(_values())).evaluate(_run())
    rendered = repr(result)
    assert "private" not in rendered
    assert "untrusted" not in rendered
    assert "سؤال" not in rendered


def test_adapter_does_not_import_ragas_at_runtime() -> None:
    assert "ragas" not in sys.modules
