"""Offline lifecycle tests for the Phase 4 BGE-M3 embedding adapter."""

from __future__ import annotations

import importlib
import math
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aaos.rag.contracts import RagErrorCode, RagOperationalError
from aaos.rag.embeddings import (
    BGE_M3_BATCH_SIZE,
    BGE_M3_DIMENSIONS,
    BGE_M3_MODEL_IDENTIFIER,
    BGE_M3_MODEL_REVISION,
    BgeM3EmbeddingService,
    FakeEmbeddingService,
)


class ControlledBackend:
    """Small, deterministic stand-in for the external FlagEmbedding model."""

    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []
        self._responses = responses or []

    def encode(self, texts: object, **kwargs: object) -> object:
        self.calls.append((texts, kwargs))
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {"dense_vecs": [[1.0, 0.0, 0.0, 0.0]]}


class ControlledBackendFactory:
    def __init__(self, backend: ControlledBackend | None = None) -> None:
        self.backend = backend or ControlledBackend()
        self.calls: list[tuple[str, str]] = []
        self.failure: Exception | None = None

    def __call__(self, snapshot_path: str, device: str) -> ControlledBackend:
        self.calls.append((snapshot_path, device))
        if self.failure is not None:
            raise self.failure
        return self.backend


class ArrayLikeDenseVectors:
    """Mimic FlagEmbedding's NumPy dense-array boundary without importing NumPy in unit tests."""

    def __init__(self, values: list[list[float]]) -> None:
        self._values = values

    def tolist(self) -> list[list[float]]:
        return self._values


class ControlledSnapshotResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.failure: Exception | None = None

    def __call__(self, model_identifier: str, revision: str, local_files_only: bool) -> str:
        self.calls.append((model_identifier, revision, local_files_only))
        if self.failure is not None:
            raise self.failure
        return "C:/controlled/bge-m3-snapshot"


def _unit_vector(dimensions: int = 4) -> list[float]:
    return [1.0, *([0.0] * (dimensions - 1))]


def _service(
    *,
    backend: ControlledBackend | None = None,
    snapshot_resolver: ControlledSnapshotResolver | None = None,
    backend_factory: ControlledBackendFactory | None = None,
) -> tuple[BgeM3EmbeddingService, ControlledSnapshotResolver, ControlledBackendFactory]:
    resolver = snapshot_resolver or ControlledSnapshotResolver()
    factory = backend_factory or ControlledBackendFactory(backend)
    return (
        BgeM3EmbeddingService(
            device="cpu",
            snapshot_resolver=resolver,
            backend_factory=factory,
        ),
        resolver,
        factory,
    )


def test_construction_is_lazy_and_metadata_is_the_pinned_bge_m3_identity() -> None:
    """Catches eager model/cache acquisition or a mutable model identity."""
    service, resolver, factory = _service()

    assert resolver.calls == []
    assert factory.calls == []
    assert service.model_metadata() == service.model_metadata().__class__(
        BGE_M3_MODEL_IDENTIFIER,
        BGE_M3_MODEL_REVISION,
        BGE_M3_DIMENSIONS,
        "cpu",
    )
    assert BGE_M3_MODEL_IDENTIFIER == "BAAI/bge-m3"
    assert BGE_M3_MODEL_REVISION == "89f985cb2fe1bfbb76fd16333196e4593a048dbd"
    assert BGE_M3_DIMENSIONS == 1024
    assert BGE_M3_BATCH_SIZE == 4


def test_default_snapshot_resolver_uses_only_the_required_local_bge_m3_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches offline resolution rejecting a usable pinned snapshot over optional repository files."""
    calls: list[tuple[str, dict[str, object]]] = []

    def snapshot_download(model_identifier: str, **kwargs: object) -> str:
        calls.append((model_identifier, kwargs))
        return "C:/controlled/bge-m3-snapshot"

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)
    from aaos.rag.embeddings import _resolve_local_snapshot

    assert _resolve_local_snapshot(BGE_M3_MODEL_IDENTIFIER, BGE_M3_MODEL_REVISION, True) == (
        "C:/controlled/bge-m3-snapshot"
    )
    assert calls == [
        (
            BGE_M3_MODEL_IDENTIFIER,
            {
                "revision": BGE_M3_MODEL_REVISION,
                "local_files_only": True,
                "allow_patterns": [
                    "config.json",
                    "config_sentence_transformers.json",
                    "model.safetensors",
                    "modules.json",
                    "sentence_bert_config.json",
                    "sentencepiece.bpe.model",
                    "special_tokens_map.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "1_Pooling/*",
                    "colbert_linear.pt",
                    "sparse_linear.pt",
                ],
            },
        )
    ]

    with pytest.raises(ValueError, match="local_files_only"):
        _resolve_local_snapshot(BGE_M3_MODEL_IDENTIFIER, BGE_M3_MODEL_REVISION, False)

    assert len(calls) == 1


@pytest.mark.parametrize("device", ["cpu", "cuda", "cuda:0", "cuda:12"])
def test_explicit_device_syntax_accepts_only_supported_cpu_cuda_forms(device: str) -> None:
    """Catches valid CUDA device forms being rejected before any lazy loading work."""
    service = BgeM3EmbeddingService(device=device)

    assert service.model_metadata().device == device


@pytest.mark.parametrize("device", ["cudaoops", "cuda:bad", "cuda:-1", "cuda:", "cuda:+1", "CUDA"])
def test_explicit_device_syntax_rejects_invalid_cuda_lookalikes(device: str) -> None:
    """Catches invalid device syntax reaching snapshot or model construction."""
    with pytest.raises(ValueError, match="device"):
        BgeM3EmbeddingService(device=device)


def test_document_embedding_uses_fixed_bounded_batches_and_dense_only_flags() -> None:
    """Catches unbounded batching or sparse/ColBERT output accidentally enabled."""
    backend = ControlledBackend(
        [
            {
                "dense_vecs": [
                    _unit_vector(BGE_M3_DIMENSIONS),
                    _unit_vector(BGE_M3_DIMENSIONS),
                    _unit_vector(BGE_M3_DIMENSIONS),
                    _unit_vector(BGE_M3_DIMENSIONS),
                ]
            },
            {"dense_vecs": [_unit_vector(BGE_M3_DIMENSIONS)]},
        ]
    )
    service, resolver, factory = _service(backend=backend)

    result = service.embed_documents(("first", "second", "third", "fourth", "fifth"))

    assert result == (tuple(_unit_vector(BGE_M3_DIMENSIONS)),) * 5
    assert resolver.calls == [(BGE_M3_MODEL_IDENTIFIER, BGE_M3_MODEL_REVISION, True)]
    assert factory.calls == [("C:/controlled/bge-m3-snapshot", "cpu")]
    assert [call[0] for call in backend.calls] == [
        ["first", "second", "third", "fourth"],
        ["fifth"],
    ]
    assert all(
        call[1]
        == {
            "batch_size": BGE_M3_BATCH_SIZE,
            "return_dense": True,
            "return_sparse": False,
            "return_colbert_vecs": False,
        }
        for call in backend.calls
    )


def test_query_embedding_uses_the_same_normalized_dense_contract() -> None:
    """Catches query embedding drift from the document-vector representation."""
    backend = ControlledBackend([{"dense_vecs": [_unit_vector(BGE_M3_DIMENSIONS)]}])
    service, _, _ = _service(backend=backend)

    assert service.embed_query("question") == tuple(_unit_vector(BGE_M3_DIMENSIONS))
    assert backend.calls[0] == (
        ["question"],
        {
            "batch_size": BGE_M3_BATCH_SIZE,
            "return_dense": True,
            "return_sparse": False,
            "return_colbert_vecs": False,
        },
    )


def test_document_embedding_accepts_flagembedding_array_like_dense_output() -> None:
    """Catches rejection of the NumPy dense array emitted by the real BGE-M3 backend."""
    backend = ControlledBackend(
        [{"dense_vecs": ArrayLikeDenseVectors([_unit_vector(BGE_M3_DIMENSIONS)])}]
    )
    service, _, _ = _service(backend=backend)

    assert service.embed_documents(("real backend shape",)) == (
        tuple(_unit_vector(BGE_M3_DIMENSIONS)),
    )


def test_document_embedding_normalizes_finite_nonzero_dense_output() -> None:
    """Catches float16 BGE-M3 output drifting from the unit-vector L2 contract."""
    backend = ControlledBackend([{"dense_vecs": [[2.0, *([0.0] * (BGE_M3_DIMENSIONS - 1))]]}])
    service, _, _ = _service(backend=backend)

    assert service.embed_documents(("normalized adapter output",)) == (
        tuple(_unit_vector(BGE_M3_DIMENSIONS)),
    )


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda service: service.embed_documents(["not-a-tuple"]), "texts"),
        (lambda service: service.embed_documents(()), "texts"),
        (lambda service: service.embed_documents(("   ",)), "texts"),
        (lambda service: service.embed_documents(("x" * 16_385,)), "texts"),
        (lambda service: service.embed_query("   "), "text"),
        (lambda service: service.embed_query("x" * 16_385), "text"),
    ],
)
def test_text_validation_rejects_invalid_values_without_leaking_content(
    operation: Callable[[BgeM3EmbeddingService], object], message: str
) -> None:
    """Catches malformed or unbounded text reaching a model backend."""
    service, resolver, factory = _service()

    with pytest.raises(ValueError, match=message) as error:
        operation(service)

    assert "x" * 100 not in str(error.value)
    assert resolver.calls == []
    assert factory.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"dense_vecs": []},
        {"dense_vecs": [_unit_vector(), _unit_vector()]},
        {"dense_vecs": [[math.nan, 0.0, 0.0, 0.0]]},
        {"dense_vecs": [[0.0] * BGE_M3_DIMENSIONS]},
        {"dense_vecs": "not-a-vector"},
    ],
)
def test_malformed_backend_output_is_a_safe_embedding_failure(payload: object) -> None:
    """Catches backend schema, dimension, finiteness, or normalization regressions."""
    service, _, _ = _service(backend=ControlledBackend([payload]))

    with pytest.raises(RagOperationalError) as error:
        service.embed_documents(("private model input",))

    assert error.value.code is RagErrorCode.EMBEDDING_FAILED
    assert str(error.value) == "rag operation failed"
    assert "private model input" not in str(error.value)


def test_release_drops_backend_then_allows_a_clean_lazy_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches lifecycle code that cannot yield BGE-M3 VRAM before generation."""
    backend = ControlledBackend(
        [
            {"dense_vecs": [_unit_vector(BGE_M3_DIMENSIONS)]},
            {"dense_vecs": [_unit_vector(BGE_M3_DIMENSIONS)]},
        ]
    )
    service, resolver, factory = _service(backend=backend)
    cleanup_calls: list[None] = []
    monkeypatch.setattr("aaos.rag.embeddings._clear_cuda_cache", lambda: cleanup_calls.append(None))

    service.embed_query("first")
    service.release_model()
    service.embed_query("second")

    assert cleanup_calls == [None]
    assert len(resolver.calls) == 2
    assert len(factory.calls) == 2


def test_close_is_idempotent_and_permanently_forbids_further_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a closed adapter that can silently reload a large model."""
    service, _, _ = _service()
    cleanup_calls: list[None] = []
    monkeypatch.setattr("aaos.rag.embeddings._clear_cuda_cache", lambda: cleanup_calls.append(None))

    service.close()
    service.close()

    assert cleanup_calls == [None]
    for operation in (
        service.model_metadata,
        lambda: service.embed_documents(("text",)),
        service.release_model,
    ):
        with pytest.raises(RagOperationalError) as error:
            operation()
        assert error.value.code is RagErrorCode.EMBEDDING_FAILED


def test_load_failure_cleans_up_and_is_safe_without_revealing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a failed model construction that leaves references or diagnostic text exposed."""
    resolver = ControlledSnapshotResolver()
    factory = ControlledBackendFactory()
    factory.failure = RuntimeError("secret cache path")
    service, _, _ = _service(snapshot_resolver=resolver, backend_factory=factory)
    cleanup_calls: list[None] = []
    monkeypatch.setattr("aaos.rag.embeddings._clear_cuda_cache", lambda: cleanup_calls.append(None))

    with pytest.raises(RagOperationalError) as error:
        service.embed_documents(("sensitive document sentence",))

    assert error.value.code is RagErrorCode.EMBEDDING_FAILED
    assert str(error.value) == "rag operation failed"
    assert "sensitive document sentence" not in str(error.value)
    assert "secret cache path" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert cleanup_calls == [None]


@pytest.mark.parametrize(
    ("operation", "backend_error", "expected_code"),
    [
        (
            "documents",
            RuntimeError("private document text and backend path"),
            RagErrorCode.EMBEDDING_FAILED,
        ),
        (
            "query",
            RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE),
            RagErrorCode.QUERY_EMBEDDING_FAILED,
        ),
    ],
)
def test_backend_failures_release_resources_map_operation_codes_and_expose_no_chain(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    backend_error: BaseException,
    expected_code: RagErrorCode,
) -> None:
    """Catches unsafe exception chaining or backend failures that retain model resources."""
    service, _, _ = _service(backend=ControlledBackend([backend_error]))
    cleanup_calls: list[None] = []
    monkeypatch.setattr("aaos.rag.embeddings._clear_cuda_cache", lambda: cleanup_calls.append(None))

    with pytest.raises(RagOperationalError) as error:
        if operation == "documents":
            service.embed_documents(("private input",))
        else:
            service.embed_query("private input")

    assert error.value.code is expected_code
    assert str(error.value) == "rag operation failed"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "private input" not in repr(error.value)
    assert "private document text" not in repr(error.value)
    assert service._backend is None
    assert cleanup_calls == [None]


def test_fake_embedding_service_is_deterministic_normalized_and_not_a_backend_fallback() -> None:
    """Catches a fake whose vectors are nondeterministic or incompatible with L2 ranking."""
    fake = FakeEmbeddingService(dimensions=4)

    first = fake.embed_documents(("same text", "other text"))
    second = fake.embed_documents(("same text", "other text"))

    assert first == second
    assert first[0] != first[1]
    assert all(math.isclose(sum(value * value for value in vector), 1.0) for vector in first)
    assert fake.model_metadata().model_identifier == "fake-embedding-service"


def test_import_has_no_model_network_cuda_or_normal_path_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an import that loads FlagEmbedding, contacts the network, or initializes state."""
    normal_database = Path.cwd() / "data" / "database" / "aaos.db"
    normal_rag = Path.cwd() / "data" / "rag"
    assert not normal_database.exists()
    assert not normal_rag.exists()

    real_import_module = importlib.import_module

    def guarded_import_module(name: str, package: str | None = None) -> Any:
        if name.startswith("FlagEmbedding") or name.startswith("torch"):
            raise AssertionError(f"forbidden import: {name}")
        return real_import_module(name, package)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("import side effect")

    monkeypatch.setattr(importlib, "import_module", guarded_import_module)
    monkeypatch.setattr(socket, "socket", forbidden)
    sys.modules.pop("aaos.rag.embeddings", None)
    module = importlib.import_module("aaos.rag.embeddings")

    assert module.BGE_M3_MODEL_IDENTIFIER == BGE_M3_MODEL_IDENTIFIER
    assert not normal_database.exists()
    assert not normal_rag.exists()
