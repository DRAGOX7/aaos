"""Lazy shared composition for the existing Phase 4 local RAG runtime."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx2

from aaos.config import CLOUD_MODEL_PROVIDERS, DEFAULT_SETTINGS, AppSettings
from aaos.inference.factory import (
    NoOpModelLifecycle,
    active_model_identifier,
    build_model_gateway,
)
from aaos.inference.gateway import ModelGateway
from aaos.inference.lm_studio import LMStudioGateway
from aaos.rag.config import RagIndexConfig, resolve_rag_runtime_paths
from aaos.rag.contracts import LocalRagService, RagErrorCode, RagOperationalError
from aaos.rag.database import (
    create_existing_rag_database_engine,
    validate_rag_database,
)
from aaos.rag.embeddings import (
    BGE_M3_DIMENSIONS,
    BGE_M3_MODEL_IDENTIFIER,
    BGE_M3_MODEL_REVISION,
)
from aaos.rag.runtime import (
    Phase4RagRuntime,
    build_phase4_runtime,
    cpu_query_embedding_factory,
)
from aaos.rag.semantic_coverage import (
    AtomicRequirementDecomposer,
    EvidenceSpanExtractor,
    RequirementCoverageClassifier,
)


@dataclass(slots=True)
class ConfiguredPhase4Runtime:
    """Own one explicitly created Phase 4 runtime and its gateway."""

    runtime: Phase4RagRuntime
    gateway: object
    service: LocalRagService
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: Exception | None = None
        try:
            asyncio.run(self.aclose())
        except Exception as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    async def aclose(self) -> None:
        """Close the runtime and gateway from an already-running event loop."""
        self.runtime.close()
        await self._close_gateway()

    async def _close_gateway(self) -> None:
        closer = getattr(self.gateway, "aclose", None)
        if not callable(closer):
            raise RuntimeError("gateway close unavailable")
        result = closer()
        if inspect.isawaitable(result):
            await result


def _semantic_factories_for(identity: str) -> dict[str, object]:
    """Factories for the three semantic steps, expecting ``identity`` as the model."""
    approved = frozenset({identity})

    def decomposer(gateway: ModelGateway) -> AtomicRequirementDecomposer:
        return AtomicRequirementDecomposer(
            gateway, model_identifier=identity, approved_identifiers=approved
        )

    def extractor(gateway: ModelGateway) -> EvidenceSpanExtractor:
        return EvidenceSpanExtractor(
            gateway, model_identifier=identity, approved_identifiers=approved
        )

    def coverage(gateway: ModelGateway) -> RequirementCoverageClassifier:
        return RequirementCoverageClassifier(
            gateway, model_identifier=identity, approved_identifiers=approved
        )

    return {
        "atomic_requirement_decomposer_factory": decomposer,
        "evidence_span_extractor_factory": extractor,
        "requirement_coverage_factory": coverage,
    }


def build_configured_phase4_runtime(
    settings: AppSettings = DEFAULT_SETTINGS,
    *,
    data_root: Path | None = None,
    on_model_failure: Callable[[str], None] | None = None,
    on_model_success: Callable[[], None] | None = None,
) -> ConfiguredPhase4Runtime:
    """Build the approved local Phase 4 runtime at an explicit call site."""

    config = RagIndexConfig(
        chunk_target_tokens=400,
        chunk_preferred_min_tokens=200,
        chunk_preferred_max_tokens=600,
        chunk_hard_max_tokens=800,
        chunk_overlap_tokens=80,
        dense_candidate_limit=30,
        lexical_candidate_limit=30,
        rrf_k=60,
        max_evidence_chunks=6,
        max_evidence_tokens=2400,
        chunker_version="heading-page-v1",
        lexical_normalizer_version="unicode-v1",
        sufficiency_policy_version="conservative-v1",
        embedding_model_identifier=BGE_M3_MODEL_IDENTIFIER,
        embedding_model_revision=BGE_M3_MODEL_REVISION,
        embedding_dimensions=BGE_M3_DIMENSIONS,
    )
    paths = resolve_rag_runtime_paths((data_root or Path("data")).resolve())
    validate_rag_database(paths.database_path)

    cloud_options: dict[str, object] = {}
    if settings.model_provider in CLOUD_MODEL_PROVIDERS:
        # Opt-in cloud model: there is no local model to load or free.
        gateway = build_model_gateway(
            settings, on_failure=on_model_failure, on_success=on_model_success
        )
        cloud_options["model_lifecycle_factory"] = NoOpModelLifecycle
        # The semantic steps reject any reply that is not from the model they expect.
        # They expect the approved local model by default; name the cloud model
        # explicitly so its replies are accepted, and nothing else is.
        cloud_options.update(_semantic_factories_for(active_model_identifier(settings)))
    else:
        gateway = LMStudioGateway(settings, httpx2.AsyncHTTPTransport())
    try:
        runtime = build_phase4_runtime(
            paths=paths,
            config=config,
            gateway=gateway,
            database_engine_factory=create_existing_rag_database_engine,
            summary_model_identifier=active_model_identifier(settings),
            query_embedding_factory=cpu_query_embedding_factory,
            **cloud_options,  # type: ignore[arg-type]
        )
    except Exception:
        try:
            asyncio.run(gateway.aclose())
        except Exception as cleanup_error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from cleanup_error
        raise
    return ConfiguredPhase4Runtime(runtime, gateway, runtime.service)
