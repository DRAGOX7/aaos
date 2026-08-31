"""Tests for the explicit local-only RAGAS acceptance bridge."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

from aaos.inference.models import ChatResponse, ModelAvailability
from aaos.rag.contracts import EmbeddingModelMetadata, EmbeddingService
from aaos.rag.evaluation.local_ragas import (
    LocalRagasEvaluatorBridge,
    RagasRunnerFactory,
)
from aaos.rag.evaluation.ragas_adapter import (
    RAGAS_METRICS,
    RagasEvaluationError,
    RagasEvaluationExample,
)

MODEL = "qwen/qwen3.5-4b"
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "89f985cb2fe1bfbb76fd16333196e4593a048dbd"


def _examples() -> tuple[RagasEvaluationExample, ...]:
    return (
        RagasEvaluationExample(
            case_id="case-001",
            question="What is the bounded answer?",
            answer="The answer is grounded.",
            contexts=("The answer is grounded.",),
            reference="The answer is grounded.",
        ),
    )


def _values() -> dict[str, float]:
    return {metric: 0.75 for metric in RAGAS_METRICS}


@dataclass
class _FakeGateway:
    closed: int = 0

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, MODEL, (MODEL,), (8192,))

    async def generate(self, request: object) -> ChatResponse:
        return ChatResponse("ready", MODEL)

    async def aclose(self) -> None:
        self.closed += 1


@dataclass
class _FakeEmbedding:
    released: int = 0
    closed: int = 0

    def model_metadata(self) -> EmbeddingModelMetadata:
        return EmbeddingModelMetadata(EMBEDDING_MODEL, EMBEDDING_REVISION, 1024, "cuda")

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0,) * 1024 for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0,) * 1024

    def release_model(self) -> None:
        self.released += 1

    def close(self) -> None:
        self.closed += 1


def _bridge(*, runner: RagasRunnerFactory | None = None) -> LocalRagasEvaluatorBridge:
    return LocalRagasEvaluatorBridge(
        gateway=_FakeGateway(),
        embedding_service=_FakeEmbedding(),
        ragas_runner=runner,
    )


def test_injected_local_bridge_executes_exact_metric_contract() -> None:
    calls: list[tuple[tuple[str, ...], object, object]] = []

    def runner(
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
        llm: object,
        embeddings: object,
    ) -> Mapping[str, object]:
        calls.append((metrics, llm, embeddings))
        assert examples == _examples()
        return _values()

    bridge = _bridge(runner=runner)
    assert bridge.evaluate(_examples(), RAGAS_METRICS) == _values()
    assert calls and calls[0][0] == RAGAS_METRICS


def test_bridge_rejects_nonapproved_metric_set() -> None:
    bridge = _bridge(runner=lambda *args: _values())
    with pytest.raises(RagasEvaluationError) as error:
        bridge.evaluate(_examples(), ("faithfulness",))
    assert str(error.value) == "INVALID_METRIC_SET"


def test_bridge_rejects_nonapproved_model_identity() -> None:
    with pytest.raises(ValueError, match="approved evaluator model"):
        LocalRagasEvaluatorBridge(
            gateway=_FakeGateway(),
            embedding_service=_FakeEmbedding(),
            evaluator_model="external-model",
            ragas_runner=lambda *args: _values(),
        )


def test_bridge_rejects_nonapproved_embedding_identity() -> None:
    class WrongEmbedding(_FakeEmbedding):
        def model_metadata(self) -> EmbeddingModelMetadata:
            return EmbeddingModelMetadata("other", "revision", 3, "cpu")

    with pytest.raises(ValueError, match="approved BGE-M3"):
        LocalRagasEvaluatorBridge(
            gateway=_FakeGateway(),
            embedding_service=WrongEmbedding(),
            ragas_runner=lambda *args: _values(),
        )


def test_embedding_setup_failure_is_sanitized() -> None:
    class FailingMetadataEmbedding(_FakeEmbedding):
        def model_metadata(self) -> EmbeddingModelMetadata:
            raise RuntimeError("private model path")

    with pytest.raises(RagasEvaluationError) as error:
        LocalRagasEvaluatorBridge(
            gateway=_FakeGateway(),
            embedding_service=FailingMetadataEmbedding(),
            ragas_runner=lambda *args: _values(),
        )
    assert str(error.value) == "LOCAL_EMBEDDING_UNAVAILABLE"
    assert "private" not in str(error.value)


def test_bridge_reports_pinned_evaluator_and_embedding_identities() -> None:
    bridge = _bridge(runner=lambda *args: _values())
    assert bridge.evaluator_model == MODEL
    assert bridge.ragas_version == "0.4.3"
    assert bridge.metric_set == RAGAS_METRICS
    assert bridge.embedding_identity == f"{EMBEDDING_MODEL}@{EMBEDDING_REVISION}"


@pytest.mark.parametrize(
    "result",
    (
        {"faithfulness": 0.5},
        {**_values(), "unexpected": 0.5},
        {metric: 1.1 for metric in RAGAS_METRICS},
        {metric: float("nan") for metric in RAGAS_METRICS},
    ),
)
def test_malformed_judge_output_fails_closed(result: Mapping[str, object]) -> None:
    bridge = _bridge(runner=lambda *args: result)
    with pytest.raises(RagasEvaluationError) as error:
        bridge.evaluate(_examples(), RAGAS_METRICS)
    assert str(error.value) == "INVALID_METRIC_OUTPUT"


def test_judge_failure_is_sanitized() -> None:
    def failing_runner(*args: object) -> Mapping[str, object]:
        raise RuntimeError("private answer and token")

    bridge = _bridge(runner=failing_runner)
    with pytest.raises(RagasEvaluationError) as error:
        bridge.evaluate(_examples(), RAGAS_METRICS)
    assert str(error.value) == "LOCAL_EVALUATION_FAILED"
    assert "private" not in str(error.value)


def test_embedding_failure_is_sanitized() -> None:
    class FailingEmbedding(_FakeEmbedding):
        def embed_query(self, text: str) -> tuple[float, ...]:
            raise RuntimeError("private embedding payload")

    embedding = FailingEmbedding()

    def runner(
        examples: tuple[RagasEvaluationExample, ...],
        metrics: tuple[str, ...],
        gateway: object,
        service: EmbeddingService,
    ) -> Mapping[str, object]:
        del examples, metrics, gateway
        service.embed_query("private query")
        return _values()

    bridge = LocalRagasEvaluatorBridge(
        gateway=_FakeGateway(),
        embedding_service=embedding,
        ragas_runner=runner,
    )
    with pytest.raises(RagasEvaluationError) as error:
        bridge.evaluate(_examples(), RAGAS_METRICS)
    assert str(error.value) == "LOCAL_EVALUATION_FAILED"
    assert "private" not in str(error.value)


def test_bridge_closes_owned_local_resources_idempotently() -> None:
    gateway = _FakeGateway()
    embedding = _FakeEmbedding()
    bridge = LocalRagasEvaluatorBridge(
        gateway=gateway,
        embedding_service=embedding,
        ragas_runner=lambda *args: _values(),
    )
    asyncio.run(bridge.aclose())
    asyncio.run(bridge.aclose())
    assert embedding.released == 1
    assert embedding.closed == 1
    assert gateway.closed == 1


def test_local_bridge_import_has_no_ragas_or_model_side_effect(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import sys
import aaos.rag.evaluation.local_ragas
assert 'ragas' not in sys.modules
assert 'torch' not in sys.modules
assert 'FlagEmbedding' not in sys.modules
print('LOCAL_RAGAS_IMPORT_SAFE')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "LOCAL_RAGAS_IMPORT_SAFE\n"
    assert result.stderr == ""


def test_external_credentials_do_not_enable_provider_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    bridge = _bridge(runner=lambda *args: _values())
    assert bridge.evaluate(_examples(), RAGAS_METRICS) == _values()


def test_local_ragas_llm_supports_bounded_metric_generation_count(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
from aaos.inference.models import ChatResponse
from aaos.rag.evaluation.local_ragas import _build_ragas_llm

class Gateway:
    async def generate(self, request):
        return ChatResponse('ready', 'qwen/qwen3.5-4b')

async def main():
    llm = _build_ragas_llm(Gateway())
    result = await llm.agenerate_text('judge', n=3)
    assert len(result.generations[0]) == 3
    print('BOUNDED_GENERATIONS_PASS')

asyncio.run(main())
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "BOUNDED_GENERATIONS_PASS\n"
    assert result.stderr == ""


def test_ragas_telemetry_is_disabled_during_local_evaluation(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import os
import ragas._analytics as analytics
from aaos.rag.evaluation.local_ragas import _disable_ragas_tracking

os.environ['RAGAS_DO_NOT_TRACK'] = 'false'
analytics.do_not_track.cache_clear()
posted = False

def fail_post(*args, **kwargs):
    global posted
    posted = True

analytics.requests.post = fail_post
with _disable_ragas_tracking():
    analytics.track(analytics.IsCompleteEvent(event_type='test', is_completed=True))
    assert analytics.do_not_track() is True
analytics.do_not_track.cache_clear()
assert analytics.do_not_track() is False
assert posted is False
print('RAGAS_TELEMETRY_DISABLED')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "RAGAS_TELEMETRY_DISABLED\n"
    assert result.stderr == ""
