"""Explicit Phase 4 local-RAG composition and lifecycle ownership."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Engine

from aaos.inference import ModelGateway
from aaos.persistence.database import create_database_engine, create_session_factory

from .chroma import ChromaDenseIndexFactory
from .chunking import DeterministicChunker
from .config import RagIndexConfig, RagRuntimePaths
from .contracts import (
    DenseIndexFactory,
    EmbeddingService,
    LocalRagService,
    RagErrorCode,
    RagIndexingService,
    RagOperationalError,
)
from .embeddings import BgeM3EmbeddingService
from .generation import CitationValidator, GroundedAnswerGenerator
from .indexing import RagDocumentIndexer, RagIndexingCoordinator
from .lock import RagIndexLock
from .repository import RagRepository
from .retrieval import EvidenceSelector, EvidenceSufficiencyGate, HybridRetriever
from .service import GuardedLocalRagService

DatabaseEngineFactory = Callable[[Any], Engine]
SessionFactoryFactory = Callable[[Engine], Callable[[], Any]]
RepositoryFactory = Callable[[Callable[[], Any]], RagRepository]
EmbeddingFactory = Callable[[RagIndexConfig], EmbeddingService]
ChunkerFactory = Callable[[RagIndexConfig], DeterministicChunker]
IndexLockFactory = Callable[[Any], RagIndexLock]


def _default_embedding_factory(_config: RagIndexConfig) -> EmbeddingService:
    return BgeM3EmbeddingService(device="cuda")


def _close_error() -> RagOperationalError:
    return RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)


@dataclass(slots=True)
class Phase4RagRuntime(AbstractContextManager["Phase4RagRuntime"]):
    """Own the resources created by one explicit Phase 4 composition.

    The public services are intentionally narrow.  Query-scoped dense handles
    are owned and closed by ``HybridRetriever``; the runtime owns the
    generation-independent factory and closes its other acquired resources in
    reverse construction order.
    """

    service: LocalRagService
    indexing: RagIndexingService
    _dense_factory: DenseIndexFactory
    _owned_closers: tuple[Callable[[], None], ...]
    _closed: bool = False

    def close(self) -> None:
        """Close every owned resource once in reverse acquisition order."""
        if self._closed:
            return
        self._closed = True
        failure = False

        for closer in reversed(self._owned_closers):
            try:
                closer()
            except Exception:
                failure = True

        if failure:
            raise _close_error()

    def __enter__(self) -> "Phase4RagRuntime":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def build_phase4_runtime(
    *,
    paths: RagRuntimePaths,
    config: RagIndexConfig,
    gateway: ModelGateway,
    database_engine_factory: DatabaseEngineFactory | None = None,
    session_factory_factory: SessionFactoryFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    dense_factory: DenseIndexFactory | None = None,
    embedding_factory: EmbeddingFactory | None = None,
    index_lock_factory: IndexLockFactory | None = None,
    chunker_factory: ChunkerFactory | None = None,
    retriever_factory: Callable[..., HybridRetriever] | None = None,
    evidence_selector_factory: Callable[..., EvidenceSelector] | None = None,
    sufficiency_gate_factory: Callable[..., EvidenceSufficiencyGate] | None = None,
    answer_generator_factory: Callable[..., GroundedAnswerGenerator] | None = None,
    citation_validator_factory: Callable[[], CitationValidator] | None = None,
    service_factory: Callable[..., LocalRagService] | None = None,
    indexing_factory: Callable[..., RagIndexingService] | None = None,
) -> Phase4RagRuntime:
    """Compose one runtime from explicit paths and injectable dependencies.

    This function is the side-effect boundary: callers deliberately choose
    paths and invoke it.  Module import and default application startup do not
    call it, load a model, open Chroma, contact LM Studio, or run migrations.
    """
    if not isinstance(paths, RagRuntimePaths):
        raise ValueError("paths must be a RagRuntimePaths")
    if not isinstance(config, RagIndexConfig):
        raise ValueError("config must be a RagIndexConfig")
    _validate_runtime_paths(paths)
    if not isinstance(gateway, ModelGateway):
        raise ValueError("gateway must implement ModelGateway")
    selected_engine_factory = database_engine_factory or create_database_engine
    selected_session_factory = session_factory_factory or create_session_factory
    selected_repository_factory = repository_factory or RagRepository
    selected_embedding_factory = embedding_factory or _default_embedding_factory
    selected_lock_factory = index_lock_factory or RagIndexLock
    selected_chunker_factory = chunker_factory or DeterministicChunker
    selected_retriever_factory = retriever_factory or HybridRetriever
    selected_selector_factory = evidence_selector_factory or EvidenceSelector
    selected_gate_factory = sufficiency_gate_factory or EvidenceSufficiencyGate
    selected_generator_factory = answer_generator_factory or GroundedAnswerGenerator
    selected_validator_factory = citation_validator_factory or CitationValidator
    selected_service_factory = service_factory or GuardedLocalRagService
    selected_indexing_factory = indexing_factory or RagIndexingCoordinator
    for factory, name in (
        (selected_engine_factory, "database_engine_factory"),
        (selected_session_factory, "session_factory_factory"),
        (selected_repository_factory, "repository_factory"),
        (selected_embedding_factory, "embedding_factory"),
        (selected_lock_factory, "index_lock_factory"),
        (selected_chunker_factory, "chunker_factory"),
        (selected_retriever_factory, "retriever_factory"),
        (selected_selector_factory, "evidence_selector_factory"),
        (selected_gate_factory, "sufficiency_gate_factory"),
        (selected_generator_factory, "answer_generator_factory"),
        (selected_validator_factory, "citation_validator_factory"),
        (selected_service_factory, "service_factory"),
        (selected_indexing_factory, "indexing_factory"),
    ):
        if not callable(factory):
            raise ValueError(f"{name} must be callable")

    engine: Engine | None = None
    embedding_service: EmbeddingService | None = None
    service: LocalRagService | None = None
    indexing: RagIndexingService | None = None
    owned_closers: list[Callable[[], None]] = []
    try:
        engine = selected_engine_factory(paths.database_path)
        _register_closer(owned_closers, engine, "dispose", "database_engine")
        _require_callable(engine, "dispose", "database_engine")
        sessions = selected_session_factory(engine)
        repository = selected_repository_factory(sessions)
        _register_optional_closer(owned_closers, repository, "close")
        for method in ("pin_query_snapshot", "lexical_search", "materialize_snapshot_chunks"):
            _require_callable(repository, method, "repository")

        selected_dense_factory = (
            dense_factory if dense_factory is not None else ChromaDenseIndexFactory(paths)
        )
        _register_optional_closer(owned_closers, selected_dense_factory, "close")
        _require_callable(selected_dense_factory, "open", "dense_factory")
        _require_callable(selected_dense_factory, "remove", "dense_factory")

        embedding_service = selected_embedding_factory(config)
        _register_closer(owned_closers, embedding_service, "close", "embedding_service")
        _require_embedding_service(embedding_service)
        _validate_embedding_identity(embedding_service, config)

        index_lock = selected_lock_factory(paths.index_lock_path)
        _register_optional_closer(owned_closers, index_lock, "close")
        chunker = selected_chunker_factory(config)
        _register_optional_closer(owned_closers, chunker, "close")
        document_indexer = RagDocumentIndexer(
            repository,
            chunker,
            embedding_service,
            selected_dense_factory,
            index_lock,
        )
        indexing = selected_indexing_factory(
            repository=repository,
            document_indexer=document_indexer,
            index_lock=index_lock,
        )
        _register_optional_closer(owned_closers, indexing, "close")
        retriever = selected_retriever_factory(
            repository=repository,
            embedding_service=embedding_service,
            dense_index_factory=selected_dense_factory,
            dense_candidate_limit=config.dense_candidate_limit,
            lexical_candidate_limit=config.lexical_candidate_limit,
            rrf_k=config.rrf_k,
        )
        _register_optional_closer(owned_closers, retriever, "close")
        evidence_selector = selected_selector_factory(config, repository=repository)
        _register_optional_closer(owned_closers, evidence_selector, "close")
        sufficiency_gate = selected_gate_factory(config)
        _register_optional_closer(owned_closers, sufficiency_gate, "close")
        answer_generator = selected_generator_factory(gateway)
        _register_optional_closer(owned_closers, answer_generator, "close")
        citation_validator = selected_validator_factory()
        _register_optional_closer(owned_closers, citation_validator, "close")
        service = selected_service_factory(
            retriever=retriever,
            evidence_selector=evidence_selector,
            sufficiency_gate=sufficiency_gate,
            answer_generator=answer_generator,
            citation_validator=citation_validator,
            rrf_k=config.rrf_k,
        )
        _register_optional_closer(owned_closers, service, "close")
        _require_callable(service, "ask", "service")
        for method in ("index_new", "rebuild", "status"):
            _require_callable(indexing, method, "indexing")
        return Phase4RagRuntime(
            service=service,
            indexing=indexing,
            _dense_factory=selected_dense_factory,
            _owned_closers=tuple(owned_closers),
        )
    except Exception as error:
        cleanup_error = _cleanup_partial(owned_closers)
        if cleanup_error is not None:
            raise error from cleanup_error
        raise


def _validate_embedding_identity(
    embedding_service: EmbeddingService,
    config: RagIndexConfig,
) -> None:
    metadata = embedding_service.model_metadata()
    if (
        metadata.model_identifier != config.embedding_model_identifier
        or metadata.model_revision != config.embedding_model_revision
        or metadata.dimensions != config.embedding_dimensions
    ):
        raise ValueError("embedding identity does not match index configuration")


def _validate_runtime_paths(paths: RagRuntimePaths) -> None:
    for path in (
        paths.database_path,
        paths.chroma_root,
        paths.index_lock_path,
        paths.evaluation_output_root,
    ):
        if not hasattr(path, "is_absolute") or not path.is_absolute():
            raise ValueError("runtime paths must be absolute")
        if path.resolve(strict=False) != path:
            raise ValueError("runtime paths must be normalized")


def _require_embedding_service(embedding_service: EmbeddingService) -> None:
    for method in ("model_metadata", "embed_documents", "embed_query", "release_model", "close"):
        _require_callable(embedding_service, method, "embedding_service")


def _require_callable(component: object, attribute: str, name: str) -> None:
    if not callable(getattr(component, attribute, None)):
        raise ValueError(f"{name} must provide {attribute}")


def _register_closer(
    owned_closers: list[Callable[[], None]],
    component: object,
    attribute: str,
    name: str,
) -> None:
    closer = getattr(component, attribute, None)
    if callable(closer):
        owned_closers.append(cast(Callable[[], None], closer))
    else:
        raise ValueError(f"{name} must provide {attribute}")


def _register_optional_closer(
    owned_closers: list[Callable[[], None]],
    component: object,
    attribute: str,
) -> None:
    closer = getattr(component, attribute, None)
    if callable(closer):
        owned_closers.append(cast(Callable[[], None], closer))


def _cleanup_partial(owned_closers: list[Callable[[], None]]) -> RagOperationalError | None:
    failure = False
    for closer in reversed(owned_closers):
        try:
            closer()
        except Exception:
            failure = True
    return _close_error() if failure else None


__all__ = ["Phase4RagRuntime", "build_phase4_runtime"]
