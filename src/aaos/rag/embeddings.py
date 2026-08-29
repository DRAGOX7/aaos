"""Offline, lifecycle-managed BGE-M3 dense embedding adapter."""

from __future__ import annotations

import gc
import hashlib
import math
from collections.abc import Callable
from typing import Protocol, cast

from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    EmbeddingModelMetadata,
    RagErrorCode,
    RagOperationalError,
)

BGE_M3_MODEL_IDENTIFIER = "BAAI/bge-m3"
BGE_M3_MODEL_REVISION = "89f985cb2fe1bfbb76fd16333196e4593a048dbd"
BGE_M3_DIMENSIONS = 1024
BGE_M3_BATCH_SIZE = 4

_BGE_M3_REQUIRED_SNAPSHOT_FILES = [
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
]


class _BgeM3Backend(Protocol):
    def encode(self, sentences: list[str], **kwargs: object) -> object: ...


SnapshotResolver = Callable[[str, str, bool], str]
BackendFactory = Callable[[str, str], object]


def _resolve_local_snapshot(model_identifier: str, revision: str, local_files_only: bool) -> str:
    """Resolve the immutable model snapshot without permitting a network request."""
    if local_files_only is not True:
        raise ValueError("local_files_only must be True")
    from huggingface_hub import snapshot_download

    return str(
        snapshot_download(
            model_identifier,
            revision=revision,
            local_files_only=True,
            allow_patterns=_BGE_M3_REQUIRED_SNAPSHOT_FILES,
        )
    )


def _construct_bge_m3_backend(snapshot_path: str, device: str) -> _BgeM3Backend:
    """Construct FlagEmbedding only after the approved local snapshot resolves."""
    from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-untyped]

    return cast(
        _BgeM3Backend,
        BGEM3FlagModel(
            snapshot_path,
            normalize_embeddings=True,
            use_fp16=device.startswith("cuda"),
            devices=device,
            batch_size=BGE_M3_BATCH_SIZE,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
            trust_remote_code=False,
        ),
    )


def _clear_cuda_cache() -> None:
    """Release cached CUDA allocations when Torch and CUDA are available."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        # Cleanup must remain safe on CPU-only hosts and during partially failed loads.
        return


def _validate_device(device: object) -> str:
    if not isinstance(device, str) or not device:
        raise ValueError("device must be a non-blank string")
    if device in {"cpu", "cuda"}:
        return device
    if (
        device.startswith("cuda:")
        and device.removeprefix("cuda:").isascii()
        and device.removeprefix("cuda:").isdigit()
    ):
        return device
    raise ValueError("device must be cpu, cuda, or cuda:<non-negative integer>")


def _validate_text(text: object, name: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{name} must be a non-blank string")
    if len(text) > MAX_RAG_TEXT_CHARACTERS:
        raise ValueError(f"{name} must not exceed {MAX_RAG_TEXT_CHARACTERS} characters")
    return text.strip()


def _validate_texts(texts: object) -> tuple[str, ...]:
    if not isinstance(texts, tuple) or not texts:
        raise ValueError("texts must be a non-empty tuple")
    return tuple(_validate_text(text, "texts") for text in texts)


class FakeEmbeddingService:
    """Deterministic, normalized fake for tests; never used by production BGE loading."""

    def __init__(
        self,
        *,
        dimensions: int = BGE_M3_DIMENSIONS,
        model_identifier: str = "fake-embedding-service",
        model_revision: str = "test-only-v1",
        device: str = "cpu",
    ) -> None:
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        self._metadata = EmbeddingModelMetadata(
            model_identifier,
            model_revision,
            dimensions,
            _validate_device(device),
        )
        self._closed = False

    def model_metadata(self) -> EmbeddingModelMetadata:
        self._require_open()
        return self._metadata

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self._require_open()
        return tuple(self._embed(text) for text in _validate_texts(texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        self._require_open()
        return self._embed(_validate_text(text, "text"))

    def release_model(self) -> None:
        self._require_open()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

    def _embed(self, text: str) -> tuple[float, ...]:
        values = [
            int.from_bytes(
                hashlib.sha256(f"{text}:{index}".encode("utf-8")).digest()[:8],
                byteorder="big",
            )
            / ((1 << 63) - 1)
            - 1.0
            for index in range(self._metadata.dimensions)
        ]
        magnitude = math.sqrt(sum(value * value for value in values))
        return tuple(value / magnitude for value in values)

    def _require_open(self) -> None:
        if self._closed:
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)


class BgeM3EmbeddingService:
    """Dense-only BGE-M3 adapter that loads only from the approved local snapshot."""

    def __init__(
        self,
        *,
        device: str,
        snapshot_resolver: SnapshotResolver = _resolve_local_snapshot,
        backend_factory: BackendFactory = _construct_bge_m3_backend,
    ) -> None:
        if not callable(snapshot_resolver):
            raise ValueError("snapshot_resolver must be callable")
        if not callable(backend_factory):
            raise ValueError("backend_factory must be callable")
        self._device = _validate_device(device)
        self._snapshot_resolver = snapshot_resolver
        self._backend_factory = backend_factory
        self._backend: _BgeM3Backend | None = None
        self._closed = False

    def model_metadata(self) -> EmbeddingModelMetadata:
        self._require_open()
        return EmbeddingModelMetadata(
            BGE_M3_MODEL_IDENTIFIER,
            BGE_M3_MODEL_REVISION,
            BGE_M3_DIMENSIONS,
            self._device,
        )

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self._require_open()
        validated_texts = _validate_texts(texts)
        vectors: list[tuple[float, ...]] = []
        failed = False
        try:
            backend = self._load_backend()
        except RagOperationalError:
            failed = True
        else:
            try:
                for offset in range(0, len(validated_texts), BGE_M3_BATCH_SIZE):
                    batch = list(validated_texts[offset : offset + BGE_M3_BATCH_SIZE])
                    vectors.extend(self._encode_batch(backend, batch))
            except Exception:
                self._release_model_resources()
                failed = True
        if failed:
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)
        if len(vectors) != len(validated_texts):
            self._release_model_resources()
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)
        return tuple(vectors)

    def embed_query(self, text: str) -> tuple[float, ...]:
        self._require_open()
        validated_text = _validate_text(text, "text")
        vectors: tuple[tuple[float, ...], ...] = ()
        failed = False
        try:
            backend = self._load_backend()
        except RagOperationalError:
            failed = True
        else:
            try:
                vectors = self._encode_batch(backend, [validated_text])
            except Exception:
                self._release_model_resources()
                failed = True
        if failed:
            raise RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED)
        if len(vectors) != 1:
            self._release_model_resources()
            raise RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED)
        return vectors[0]

    def release_model(self) -> None:
        self._require_open()
        self._release_model_resources()

    def _release_model_resources(self) -> None:
        self._backend = None
        gc.collect()
        _clear_cuda_cache()

    def close(self) -> None:
        if self._closed:
            return
        self.release_model()
        self._closed = True

    def _load_backend(self) -> _BgeM3Backend:
        if self._backend is not None:
            return self._backend
        failed = False
        try:
            snapshot_path = self._snapshot_resolver(
                BGE_M3_MODEL_IDENTIFIER,
                BGE_M3_MODEL_REVISION,
                True,
            )
            if not isinstance(snapshot_path, str) or not snapshot_path.strip():
                raise ValueError("invalid local model snapshot")
            backend = self._backend_factory(snapshot_path, self._device)
            if not callable(getattr(backend, "encode", None)):
                raise ValueError("invalid BGE-M3 backend")
            self._backend = cast(_BgeM3Backend, backend)
        except Exception:
            self._release_model_resources()
            failed = True
        if failed:
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)
        if self._backend is None:
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)
        return self._backend

    @staticmethod
    def _encode_batch(backend: _BgeM3Backend, texts: list[str]) -> tuple[tuple[float, ...], ...]:
        result = backend.encode(
            texts,
            batch_size=BGE_M3_BATCH_SIZE,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        if not isinstance(result, dict):
            raise ValueError("invalid BGE-M3 output")
        dense_vectors = result.get("dense_vecs")
        tolist = getattr(dense_vectors, "tolist", None)
        if callable(tolist):
            dense_vectors = tolist()
        if not isinstance(dense_vectors, (list, tuple)) or len(dense_vectors) != len(texts):
            raise ValueError("invalid BGE-M3 dense output")
        return tuple(BgeM3EmbeddingService._validate_vector(vector) for vector in dense_vectors)

    @staticmethod
    def _validate_vector(vector: object) -> tuple[float, ...]:
        if not isinstance(vector, (list, tuple)) or len(vector) != BGE_M3_DIMENSIONS:
            raise ValueError("invalid BGE-M3 dense vector")
        values: list[float] = []
        for value in vector:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("invalid BGE-M3 dense vector")
            values.append(float(value))
        squared_norm = sum(value * value for value in values)
        if not math.isfinite(squared_norm) or squared_norm <= 0.0:
            raise ValueError("BGE-M3 dense vector must have a positive norm")
        magnitude = math.sqrt(squared_norm)
        return tuple(value / magnitude for value in values)

    def _require_open(self) -> None:
        if self._closed:
            raise RagOperationalError(RagErrorCode.EMBEDDING_FAILED)
