"""Task 20 runtime composition and ownership tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from aaos.inference import ChatMessage, ChatRequest, ChatResponse, ModelAvailability
from aaos.rag.config import RagIndexConfig, RagRuntimePaths, compute_config_fingerprint
from aaos.rag.contracts import (
    EmbeddingModelMetadata,
    RagGenerationIdentity,
)
from aaos.rag.repository import RagRepository


def _config(**changes: object) -> RagIndexConfig:
    values: dict[str, object] = {
        "chunk_target_tokens": 400,
        "chunk_preferred_min_tokens": 200,
        "chunk_preferred_max_tokens": 600,
        "chunk_hard_max_tokens": 800,
        "chunk_overlap_tokens": 80,
        "dense_candidate_limit": 30,
        "lexical_candidate_limit": 30,
        "rrf_k": 60,
        "max_evidence_chunks": 6,
        "max_evidence_tokens": 2400,
        "chunker_version": "heading-page-v1",
        "lexical_normalizer_version": "unicode-v1",
        "sufficiency_policy_version": "conservative-v1",
        "embedding_model_identifier": "fake-embedding",
        "embedding_model_revision": "test-v1",
        "embedding_dimensions": 4,
    }
    values.update(changes)
    return RagIndexConfig(**values)  # type: ignore[arg-type]


GENERATION = RagGenerationIdentity(1, compute_config_fingerprint(_config()), 0, 4)


class _Engine:
    def __init__(self, events: list[str], *, fail_dispose: bool = False) -> None:
        self.events = events
        self.fail_dispose = fail_dispose

    def dispose(self) -> None:
        self.events.append("engine.dispose")
        if self.fail_dispose:
            raise RuntimeError("engine cleanup failure")


class _Dense:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("dense.close")


class _DenseFactory:
    def __init__(self, dense: _Dense) -> None:
        self.dense = dense
        self.opened: list[RagGenerationIdentity] = []

    def open(self, generation: RagGenerationIdentity) -> _Dense:
        self.opened.append(generation)
        return self.dense

    def remove(self, _generation: RagGenerationIdentity) -> None:
        return None


class _Embedding:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def model_metadata(self) -> EmbeddingModelMetadata:
        return EmbeddingModelMetadata("fake-embedding", "test-v1", 4, "cpu")

    def embed_documents(self, _texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()

    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0, 0.0, 0.0)

    def release_model(self) -> None:
        return None

    def close(self) -> None:
        self.events.append("embedding.close")


class _Repository(RagRepository):
    def __init__(self) -> None:
        def session_factory() -> Any:
            return None

        super().__init__(session_factory)

    def get_active_generation(self) -> Any:
        return GENERATION


class _NoActiveRepository(_Repository):
    def get_active_generation(self) -> Any:
        raise AssertionError("runtime must not resolve an active generation")


class _Service:
    async def ask(self, _query: object) -> object:
        return None


class _Indexing:
    def index_new(self, document_version_id: int | None = None) -> object:
        return document_version_id

    def rebuild(self) -> object:
        return None

    def status(self) -> object:
        return None


class _ClosableService(_Service):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("service.close")


class _ClosableIndexing(_Indexing):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("indexing.close")


class _Gateway:
    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, "fake", ("fake",), (1024,))

    async def generate(self, _request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="{}", model_identifier="fake")


class _Lifecycle:
    def __init__(
        self, events: list[str], *, fail_unload: bool = False, fail_load: bool = False
    ) -> None:
        self.events = events
        self.fail_unload = fail_unload
        self.fail_load = fail_load

    def ensure_answer_model_unloaded(self) -> None:
        self.events.append("model.unload")
        if self.fail_unload:
            raise RuntimeError("unload failure")

    def ensure_answer_model_loaded(self) -> None:
        self.events.append("model.load")
        if self.fail_load:
            raise RuntimeError("load failure")

    def close(self) -> None:
        self.events.append("model.close")


class _EventEmbedding(_Embedding):
    def embed_query(self, text: str) -> tuple[float, ...]:
        self.events.append("bge.embed")
        return super().embed_query(text)

    def release_model(self) -> None:
        self.events.append("bge.release")
        super().release_model()


class _EventGateway(_Gateway):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.events.append("gateway.generate")
        return await super().generate(request)


class _BrokenDenseFactory:
    def remove(self, _generation: object) -> None:
        return None


def _paths(tmp_path: Path) -> RagRuntimePaths:
    return RagRuntimePaths(
        tmp_path / "database" / "rag.db",
        tmp_path / "chroma",
        tmp_path / "index.lock",
        tmp_path / "evaluations",
    )


def _build(
    tmp_path: Path,
    events: list[str],
    *,
    service_factory: Any | None = None,
    embedding_factory: Any | None = None,
    dense_factory: Any | None = None,
    indexing_factory: Any | None = None,
    use_default_services: bool = False,
) -> Any:
    from aaos.rag.runtime import build_phase4_runtime

    engine = _Engine(events)
    dense = _Dense(events)
    embedding = _Embedding(events)
    repository = _Repository()
    dependencies: dict[str, Any] = {
        "paths": _paths(tmp_path),
        "config": _config(),
        "gateway": _Gateway(),
        "database_engine_factory": lambda _path: cast(Any, engine),
        "session_factory_factory": lambda _engine: cast(Any, object()),
        "repository_factory": lambda _sessions: repository,
        "dense_factory": cast(Any, dense_factory or _DenseFactory(dense)),
        "embedding_factory": embedding_factory or (lambda _config: embedding),
    }
    if not use_default_services:
        dependencies.update(
            {
                "service_factory": cast(
                    Any, service_factory or (lambda **_dependencies: _Service())
                ),
                "indexing_factory": cast(
                    Any, indexing_factory or (lambda **_dependencies: _Indexing())
                ),
            }
        )
    return build_phase4_runtime(**dependencies)


def test_runtime_closes_owned_resources_in_order_and_is_idempotent(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = _build(tmp_path, events)

    runtime.close()
    runtime.close()

    assert events == ["embedding.close", "engine.dispose"]


def test_runtime_context_manager_closes_resources(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = _build(tmp_path, events)

    with runtime as active:
        assert active is runtime

    assert events == ["embedding.close", "engine.dispose"]


def test_runtime_stops_owned_services_before_backend_resources(tmp_path: Path) -> None:
    events: list[str] = []

    runtime = _build(
        tmp_path,
        events,
        service_factory=lambda **_dependencies: _ClosableService(events),
        indexing_factory=lambda **_dependencies: _ClosableIndexing(events),
    )

    runtime.close()

    assert events == ["service.close", "indexing.close", "embedding.close", "engine.dispose"]


def test_construction_failure_closes_only_acquired_resources(tmp_path: Path) -> None:
    events: list[str] = []

    def fail_service(**_dependencies: object) -> object:
        raise RuntimeError("construction failure")

    with pytest.raises(RuntimeError, match="construction failure"):
        _build(tmp_path, events, service_factory=fail_service)

    assert events == ["embedding.close", "engine.dispose"]


def test_embedding_factory_failure_does_not_close_unacquired_embedding(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def fail_embedding(_config: RagIndexConfig) -> object:
        raise RuntimeError("embedding construction failure")

    with pytest.raises(RuntimeError, match="embedding construction failure"):
        _build(tmp_path, events, embedding_factory=fail_embedding)

    assert events == ["engine.dispose"]


def test_runtime_does_not_require_active_generation_at_construction(tmp_path: Path) -> None:
    events: list[str] = []
    dense_factory = _DenseFactory(_Dense(events))
    from aaos.rag.runtime import build_phase4_runtime

    runtime = build_phase4_runtime(
        paths=_paths(tmp_path),
        config=_config(),
        gateway=_Gateway(),
        database_engine_factory=lambda _path: cast(Any, _Engine(events)),
        session_factory_factory=lambda _engine: cast(Any, object()),
        repository_factory=lambda _sessions: _NoActiveRepository(),
        dense_factory=cast(Any, dense_factory),
        embedding_factory=lambda _config: _Embedding(events),
        service_factory=cast(Any, lambda **_dependencies: _Service()),
        indexing_factory=cast(Any, lambda **_dependencies: _Indexing()),
    )

    assert dense_factory.opened == []
    runtime.close()
    assert events == ["embedding.close", "engine.dispose"]


def test_runtime_close_attempts_all_resources_and_reports_safe_failure(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = _build(
        tmp_path,
        events,
        service_factory=lambda **_dependencies: _ClosableService(events),
        indexing_factory=lambda **_dependencies: _ClosableIndexing(events),
    )
    runtime._owned_closers = (
        lambda: (_ for _ in ()).throw(RuntimeError("service cleanup failure")),
        lambda: events.append("indexing.close"),
        lambda: events.append("embedding.close"),
        lambda: events.append("engine.dispose"),
    )

    from aaos.rag.contracts import RagErrorCode, RagOperationalError

    with pytest.raises(RagOperationalError) as caught:
        runtime.close()

    assert caught.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert events == ["engine.dispose", "embedding.close", "indexing.close"]


def test_construction_cleanup_failure_is_observable_without_replacing_primary_error(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    engine = _Engine(events, fail_dispose=True)

    def fail_service(**_dependencies: object) -> object:
        raise RuntimeError("construction failure")

    from aaos.rag.runtime import build_phase4_runtime

    with pytest.raises(RuntimeError, match="construction failure") as caught:
        build_phase4_runtime(
            paths=_paths(tmp_path),
            config=_config(),
            gateway=_Gateway(),
            database_engine_factory=lambda _path: cast(Any, engine),
            session_factory_factory=lambda _engine: cast(Any, object()),
            repository_factory=lambda _sessions: _Repository(),
            dense_factory=cast(Any, _DenseFactory(_Dense(events))),
            embedding_factory=lambda _config: _Embedding(events),
            service_factory=cast(Any, fail_service),
            indexing_factory=cast(Any, lambda **_dependencies: _Indexing()),
        )

    from aaos.rag.contracts import RagErrorCode, RagOperationalError

    assert isinstance(caught.value.__cause__, RagOperationalError)
    assert caught.value.__cause__.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert events == ["embedding.close", "engine.dispose"]


@pytest.mark.parametrize(
    "stage",
    (
        "engine",
        "session",
        "repository",
        "dense_factory",
        "embedding",
        "lock",
        "chunker",
        "indexing",
        "retriever",
        "selector",
        "gate",
        "generator",
        "validator",
        "service",
    ),
)
def test_construction_failure_unwinds_each_owned_stage(tmp_path: Path, stage: str) -> None:
    events: list[str] = []
    engine = _Engine(events)
    embedding = _Embedding(events)
    indexing = _ClosableIndexing(events)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(stage)

    from aaos.rag.runtime import build_phase4_runtime

    dependencies: dict[str, Any] = {
        "paths": _paths(tmp_path),
        "config": _config(),
        "gateway": _Gateway(),
        "database_engine_factory": (fail if stage == "engine" else lambda _path: engine),
        "session_factory_factory": (fail if stage == "session" else lambda _engine: object()),
        "repository_factory": (fail if stage == "repository" else lambda _sessions: _Repository()),
        "dense_factory": (
            cast(Any, _BrokenDenseFactory())
            if stage == "dense_factory"
            else cast(Any, _DenseFactory(_Dense(events)))
        ),
        "embedding_factory": (fail if stage == "embedding" else lambda _config: embedding),
        "index_lock_factory": (fail if stage == "lock" else lambda _path: object()),
        "chunker_factory": (fail if stage == "chunker" else lambda _config: object()),
        "indexing_factory": (fail if stage == "indexing" else lambda **_dependencies: indexing),
        "retriever_factory": (fail if stage == "retriever" else lambda **_dependencies: object()),
        "evidence_selector_factory": (
            fail if stage == "selector" else lambda *_args, **_kwargs: object()
        ),
        "sufficiency_gate_factory": (fail if stage == "gate" else lambda *_args: object()),
        "answer_generator_factory": (fail if stage == "generator" else lambda _gateway: object()),
        "citation_validator_factory": (fail if stage == "validator" else lambda: object()),
        "service_factory": (fail if stage == "service" else lambda **_dependencies: _Service()),
    }

    with pytest.raises((RuntimeError, ValueError), match=stage):
        build_phase4_runtime(**dependencies)

    if stage == "engine":
        assert events == []
    elif stage in {"session", "repository", "dense_factory", "embedding"}:
        assert events == ["engine.dispose"]
    elif stage in {"lock", "chunker", "indexing"}:
        assert events == ["embedding.close", "engine.dispose"]
    else:
        assert events == ["indexing.close", "embedding.close", "engine.dispose"]


def test_relative_runtime_path_fails_before_engine_creation(tmp_path: Path) -> None:
    events: list[str] = []
    from aaos.rag.runtime import build_phase4_runtime

    with pytest.raises(ValueError, match="absolute"):
        build_phase4_runtime(
            paths=RagRuntimePaths(
                Path("relative.db"),
                tmp_path / "chroma",
                tmp_path / "lock",
                tmp_path / "evaluations",
            ),
            config=_config(),
            gateway=_Gateway(),
            database_engine_factory=lambda _path: cast(Any, _Engine(events)),
        )

    assert events == []


def test_builder_uses_injected_paths_and_never_defaults_to_normal_storage(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    runtime = _build(tmp_path, events)

    assert not Path("data/database/aaos.db").exists()
    assert not Path("data/rag").exists()
    runtime.close()


def test_builder_composes_the_approved_services_without_reimplementing_them(
    tmp_path: Path,
) -> None:
    from aaos.rag.service import GuardedLocalRagService

    events: list[str] = []
    runtime = _build(tmp_path, events, use_default_services=True)

    assert isinstance(runtime.service, GuardedLocalRagService)
    assert runtime.service._generation_policy_version == "grounded-v1"
    assert runtime.indexing.__class__.__name__ == "RagIndexingCoordinator"
    runtime.close()


def test_runtime_lifecycle_orders_model_residency_around_embedding_and_generation() -> None:
    from aaos.rag.runtime import _ManagedEmbeddingService, _ManagedModelGateway

    events: list[str] = []
    lifecycle = _Lifecycle(events)
    embedding = _ManagedEmbeddingService(_EventEmbedding(events), lifecycle)
    gateway = _ManagedModelGateway(_EventGateway(events), lifecycle, embedding)

    embedding.embed_query("question")
    embedding.release_model()
    asyncio.run(
        gateway.generate(ChatRequest(messages=(ChatMessage(role="user", content="question"),)))
    )

    assert events == [
        "model.unload",
        "bge.embed",
        "bge.release",
        "model.load",
        "gateway.generate",
        "model.unload",
    ]


def test_unload_failure_blocks_bge_acquisition() -> None:
    from aaos.rag.contracts import RagErrorCode, RagOperationalError
    from aaos.rag.runtime import _ManagedEmbeddingService

    events: list[str] = []
    embedding = _ManagedEmbeddingService(
        _EventEmbedding(events), _Lifecycle(events, fail_unload=True)
    )

    with pytest.raises(RagOperationalError) as caught:
        embedding.embed_query("question")

    assert caught.value.code is RagErrorCode.MODEL_UNAVAILABLE
    assert "bge.embed" not in events


def test_release_failure_blocks_generation() -> None:
    from aaos.rag.contracts import RagErrorCode, RagOperationalError
    from aaos.rag.runtime import _ManagedEmbeddingService, _ManagedModelGateway

    events: list[str] = []

    class _FailingRelease(_EventEmbedding):
        def release_model(self) -> None:
            self.events.append("bge.release")
            raise RuntimeError("release failure")

    embedding = _ManagedEmbeddingService(_FailingRelease(events), _Lifecycle(events))
    gateway = _ManagedModelGateway(_EventGateway(events), _Lifecycle(events), embedding)
    embedding.embed_query("question")
    with pytest.raises(RuntimeError, match="release failure"):
        embedding.release_model()

    with pytest.raises(RagOperationalError) as caught:
        asyncio.run(
            gateway.generate(ChatRequest(messages=(ChatMessage(role="user", content="question"),)))
        )

    assert caught.value.code is RagErrorCode.MODEL_UNAVAILABLE
    assert "gateway.generate" not in events


def test_load_failure_blocks_generation() -> None:
    from aaos.rag.contracts import RagErrorCode, RagOperationalError
    from aaos.rag.runtime import _ManagedEmbeddingService, _ManagedModelGateway

    events: list[str] = []
    lifecycle = _Lifecycle(events, fail_load=True)
    embedding = _ManagedEmbeddingService(_EventEmbedding(events), lifecycle)
    gateway = _ManagedModelGateway(_EventGateway(events), lifecycle, embedding)
    embedding.release_model()

    with pytest.raises(RagOperationalError) as caught:
        asyncio.run(
            gateway.generate(ChatRequest(messages=(ChatMessage(role="user", content="question"),)))
        )

    assert caught.value.code is RagErrorCode.MODEL_UNAVAILABLE
    assert "gateway.generate" not in events
