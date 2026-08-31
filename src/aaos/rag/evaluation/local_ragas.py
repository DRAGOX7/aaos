"""Explicit local-only RAGAS evaluator bridge for acceptance runs.

The bridge is intentionally separate from the production query path.  It accepts
already-owned local inference components, never discovers providers, and imports
RAGAS only when an evaluation is actually requested.
"""

from __future__ import annotations

import asyncio
import importlib
import math
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Protocol, TypeAlias, cast

from aaos.inference import ChatMessage, ChatRequest, ModelGateway
from aaos.rag.contracts import EmbeddingService

from ..embeddings import BGE_M3_DIMENSIONS, BGE_M3_MODEL_IDENTIFIER, BGE_M3_MODEL_REVISION
from .ragas_adapter import (
    RAGAS_METRICS,
    RAGAS_VERSION,
    LocalRagasEvaluator,
    RagasEvaluationError,
    RagasEvaluationExample,
)

LOCAL_RAGAS_EVALUATOR_MODEL = "qwen/qwen3.5-4b"
LOCAL_RAGAS_EMBEDDING_MODEL = BGE_M3_MODEL_IDENTIFIER
LOCAL_RAGAS_EMBEDDING_REVISION = BGE_M3_MODEL_REVISION
LOCAL_RAGAS_EMBEDDING_DIMENSIONS = BGE_M3_DIMENSIONS
LOCAL_RAGAS_MAX_GENERATIONS = 3


class RagasRunner(Protocol):
    """Injected runner seam used by deterministic tests and the local adapter."""

    def __call__(
        self,
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
        gateway: ModelGateway,
        embedding_service: EmbeddingService,
    ) -> Mapping[str, object]: ...


RagasRunnerFactory: TypeAlias = Callable[
    [tuple[RagasEvaluationExample, ...], tuple[str, ...], ModelGateway, EmbeddingService],
    Mapping[str, object],
]


def _validate_examples(examples: object) -> tuple[RagasEvaluationExample, ...]:
    if not isinstance(examples, tuple) or not examples:
        raise RagasEvaluationError("INVALID_EVALUATION_INPUT")
    if not all(isinstance(example, RagasEvaluationExample) for example in examples):
        raise RagasEvaluationError("INVALID_EVALUATION_INPUT")
    return examples


def _validate_metrics(metrics: object) -> tuple[str, ...]:
    if not isinstance(metrics, tuple) or metrics != RAGAS_METRICS:
        raise RagasEvaluationError("INVALID_METRIC_SET")
    return metrics


def _validate_metric_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("metric value is not numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError("metric value is outside the approved range")
    return normalized


def _metric_mean(value: object) -> float:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = tuple(_validate_metric_value(item) for item in value)
        if not values:
            raise ValueError("metric output is empty")
        return math.fsum(values) / len(values)
    return _validate_metric_value(value)


def _validate_metric_output(output: object, metrics: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(output, Mapping) or set(output) != set(metrics):
        raise RagasEvaluationError("INVALID_METRIC_OUTPUT")
    try:
        return {name: _metric_mean(output[name]) for name in metrics}
    except (KeyError, TypeError, ValueError):
        raise RagasEvaluationError("INVALID_METRIC_OUTPUT") from None


def _prompt_text(prompt: object) -> str:
    if isinstance(prompt, str):
        text = prompt
    else:
        to_string = getattr(prompt, "to_string", None)
        text = to_string() if callable(to_string) else ""
    if not isinstance(text, str) or not text.strip():
        raise RagasEvaluationError("LOCAL_JUDGE_FAILED")
    return text


def _run_sync(coroutine: Any) -> Any:
    """Run the gateway coroutine from RAGAS' synchronous legacy boundary."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    raise RagasEvaluationError("LOCAL_JUDGE_FAILED")


def _build_ragas_llm(gateway: ModelGateway) -> Any:
    """Build a legacy RAGAS LLM wrapper without importing RAGAS at module load."""
    langchain_outputs = importlib.import_module("langchain_core.outputs")
    Generation = langchain_outputs.Generation
    LLMResult = langchain_outputs.LLMResult
    ragas_llms = importlib.import_module("ragas.llms.base")
    BaseRagasLLM = cast(Any, ragas_llms.BaseRagasLLM)

    class _GatewayRagasLLM(BaseRagasLLM):  # type: ignore[valid-type,misc]
        def __init__(self, local_gateway: ModelGateway) -> None:
            super().__init__()
            self._gateway = local_gateway

        def generate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float = 0.01,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            if (
                not isinstance(n, int)
                or isinstance(n, bool)
                or not 1 <= n <= LOCAL_RAGAS_MAX_GENERATIONS
            ):
                raise RagasEvaluationError("UNSUPPORTED_LLM_REQUEST")
            return _run_sync(self.agenerate_text(prompt, n, temperature, stop, callbacks))

        async def agenerate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float | None = 0.01,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            del temperature, stop, callbacks
            if (
                not isinstance(n, int)
                or isinstance(n, bool)
                or not 1 <= n <= LOCAL_RAGAS_MAX_GENERATIONS
            ):
                raise RagasEvaluationError("UNSUPPORTED_LLM_REQUEST")
            request = ChatRequest(
                messages=(ChatMessage(role="user", content=_prompt_text(prompt)),),
            )
            generations: list[Any] = []
            for _ in range(n):
                try:
                    response = await self._gateway.generate(request)
                except RagasEvaluationError:
                    raise
                except Exception:
                    raise RagasEvaluationError("LOCAL_JUDGE_FAILED") from None
                if response.model_identifier != LOCAL_RAGAS_EVALUATOR_MODEL:
                    raise RagasEvaluationError("LOCAL_MODEL_IDENTITY_MISMATCH")
                generations.append(Generation(text=response.content))
            return LLMResult(generations=[generations])

        def is_finished(self, response: Any) -> bool:
            generations = getattr(response, "generations", None)
            if not isinstance(generations, list) or not generations:
                return False
            first = generations[0]
            if not isinstance(first, list) or not first:
                return False
            return bool(getattr(first[0], "text", "").strip())

    return _GatewayRagasLLM(gateway)


def _build_ragas_embeddings(embedding_service: EmbeddingService) -> Any:
    """Build a RAGAS embedding wrapper over the approved AAOS service."""
    ragas_embeddings = importlib.import_module("ragas.embeddings.base")
    BaseRagasEmbeddings = cast(Any, ragas_embeddings.BaseRagasEmbeddings)

    class _EmbeddingRagasAdapter(BaseRagasEmbeddings):  # type: ignore[valid-type,misc]
        def __init__(self, service: EmbeddingService) -> None:
            super().__init__()
            self._service = service

        def embed_query(self, text: str) -> list[float]:
            return list(self._service.embed_query(text))

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            values = self._service.embed_documents(tuple(texts))
            return [list(value) for value in values]

        async def aembed_query(self, text: str) -> list[float]:
            return self.embed_query(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return self.embed_documents(texts)

    return _EmbeddingRagasAdapter(embedding_service)


@contextmanager
def _disable_ragas_tracking() -> Iterator[None]:
    """Prevent RAGAS telemetry from leaving the local evaluation process."""
    key = "RAGAS_DO_NOT_TRACK"
    previous = os.environ.get(key)
    os.environ[key] = "true"
    analytics = sys.modules.get("ragas._analytics")
    try:
        cache_clear = getattr(getattr(analytics, "do_not_track", None), "cache_clear", None)
        if callable(cache_clear):
            cache_clear()
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
        analytics = sys.modules.get("ragas._analytics")
        cache_clear = getattr(getattr(analytics, "do_not_track", None), "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def _run_pinned_ragas(
    examples: tuple[RagasEvaluationExample, ...],
    metrics: tuple[str, ...],
    gateway: ModelGateway,
    embedding_service: EmbeddingService,
) -> Mapping[str, object]:
    with _disable_ragas_tracking():
        return _run_pinned_ragas_impl(examples, metrics, gateway, embedding_service)


def _run_pinned_ragas_impl(
    examples: tuple[RagasEvaluationExample, ...],
    metrics: tuple[str, ...],
    gateway: ModelGateway,
    embedding_service: EmbeddingService,
) -> Mapping[str, object]:
    """Run exactly the pinned legacy RAGAS metric set with local wrappers."""
    try:
        ragas_module = importlib.import_module("ragas")
        evaluation_dataset = ragas_module.EvaluationDataset
        evaluate = ragas_module.evaluate
        answer_relevance = importlib.import_module("ragas.metrics._answer_relevance")
        context_precision = importlib.import_module("ragas.metrics._context_precision")
        context_recall = importlib.import_module("ragas.metrics._context_recall")
        faithfulness = importlib.import_module("ragas.metrics._faithfulness")

        rows: list[dict[str, object]] = []
        for example in examples:
            row: dict[str, object] = {
                "user_input": example.question,
                "response": example.answer,
                "retrieved_contexts": list(example.contexts),
            }
            if example.reference is not None:
                row["reference"] = example.reference
            rows.append(row)
        dataset = evaluation_dataset.from_list(rows)
        llm = _build_ragas_llm(gateway)
        embeddings = _build_ragas_embeddings(embedding_service)
        metric_objects: dict[str, Any] = {
            "faithfulness": faithfulness.Faithfulness(llm=llm),
            "answer_relevancy": answer_relevance.AnswerRelevancy(llm=llm, embeddings=embeddings),
            "context_precision": context_precision.ContextPrecision(llm=llm),
            "context_recall": context_recall.ContextRecall(llm=llm),
        }
        result = evaluate(
            dataset,
            metrics=[metric_objects[name] for name in metrics],
            raise_exceptions=True,
            show_progress=False,
        )
        scores = getattr(result, "scores", None)
        if not isinstance(scores, list) or not scores:
            raise ValueError("RAGAS returned no scores")
        aggregate: dict[str, list[object]] = {name: [] for name in metrics}
        for row in scores:
            if not isinstance(row, Mapping):
                raise ValueError("RAGAS returned malformed scores")
            for name in metrics:
                if name not in row:
                    raise ValueError("RAGAS omitted an approved metric")
                aggregate[name].append(row[name])
        return aggregate
    except RagasEvaluationError:
        raise
    except Exception:
        raise RagasEvaluationError("LOCAL_EVALUATION_FAILED") from None


class LocalRagasEvaluatorBridge(LocalRagasEvaluator):
    """Own one explicit local RAGAS evaluator and its local model resources."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        embedding_service: EmbeddingService,
        evaluator_model: str = LOCAL_RAGAS_EVALUATOR_MODEL,
        ragas_runner: RagasRunnerFactory | None = None,
    ) -> None:
        if evaluator_model != LOCAL_RAGAS_EVALUATOR_MODEL:
            raise ValueError("only the approved evaluator model is supported")
        if not callable(getattr(gateway, "generate", None)):
            raise ValueError("gateway must provide local generation")
        if not callable(getattr(gateway, "aclose", None)):
            raise ValueError("gateway must provide deterministic close")
        try:
            metadata = embedding_service.model_metadata()
        except Exception:
            raise RagasEvaluationError("LOCAL_EMBEDDING_UNAVAILABLE") from None
        if (
            metadata.model_identifier != LOCAL_RAGAS_EMBEDDING_MODEL
            or metadata.model_revision != LOCAL_RAGAS_EMBEDDING_REVISION
            or metadata.dimensions != LOCAL_RAGAS_EMBEDDING_DIMENSIONS
        ):
            raise ValueError("embedding service must use approved BGE-M3")
        self._gateway = gateway
        self._embedding_service = embedding_service
        self._ragas_runner = ragas_runner or _run_pinned_ragas
        self._closed = False

    @property
    def evaluator_model(self) -> str:
        return LOCAL_RAGAS_EVALUATOR_MODEL

    @property
    def ragas_version(self) -> str:
        return RAGAS_VERSION

    @property
    def metric_set(self) -> tuple[str, ...]:
        return RAGAS_METRICS

    @property
    def embedding_identity(self) -> str:
        return f"{LOCAL_RAGAS_EMBEDDING_MODEL}@{LOCAL_RAGAS_EMBEDDING_REVISION}"

    def evaluate(
        self,
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
    ) -> Mapping[str, object]:
        if self._closed:
            raise RagasEvaluationError("LOCAL_EVALUATOR_CLOSED")
        valid_examples = _validate_examples(examples)
        valid_metrics = _validate_metrics(metrics)
        try:
            output = self._ragas_runner(
                valid_examples,
                valid_metrics,
                self._gateway,
                self._embedding_service,
            )
        except RagasEvaluationError:
            raise
        except Exception:
            raise RagasEvaluationError("LOCAL_EVALUATION_FAILED") from None
        return _validate_metric_output(output, valid_metrics)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        try:
            self._embedding_service.release_model()
        except Exception:
            failed = True
        try:
            self._embedding_service.close()
        except Exception:
            failed = True
        try:
            await cast(Any, self._gateway).aclose()
        except Exception:
            failed = True
        if failed:
            raise RagasEvaluationError("LOCAL_RAGAS_CLEANUP_FAILED")

    async def __aenter__(self) -> "LocalRagasEvaluatorBridge":
        if self._closed:
            raise RagasEvaluationError("LOCAL_EVALUATOR_CLOSED")
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()


def create_local_ragas_evaluator(
    *,
    gateway: ModelGateway,
    embedding_service: EmbeddingService,
    ragas_runner: RagasRunnerFactory | None = None,
) -> LocalRagasEvaluatorBridge:
    """Create the explicitly injected evaluator used only by acceptance code."""
    return LocalRagasEvaluatorBridge(
        gateway=gateway,
        embedding_service=embedding_service,
        ragas_runner=ragas_runner,
    )


__all__ = [
    "LOCAL_RAGAS_EMBEDDING_DIMENSIONS",
    "LOCAL_RAGAS_EMBEDDING_MODEL",
    "LOCAL_RAGAS_EMBEDDING_REVISION",
    "LOCAL_RAGAS_EVALUATOR_MODEL",
    "LOCAL_RAGAS_MAX_GENERATIONS",
    "LocalRagasEvaluatorBridge",
    "RagasRunner",
    "RagasRunnerFactory",
    "create_local_ragas_evaluator",
]
