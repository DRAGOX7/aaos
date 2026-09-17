"""Lazy shared composition for the existing Phase 4 local RAG runtime."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

import httpx2

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.inference.lm_studio import LMStudioGateway
from aaos.rag.config import RagIndexConfig, resolve_rag_runtime_paths
from aaos.rag.contracts import LocalRagService, RagErrorCode, RagOperationalError
from aaos.rag.embeddings import (
    BGE_M3_DIMENSIONS,
    BGE_M3_MODEL_IDENTIFIER,
    BGE_M3_MODEL_REVISION,
)
from aaos.rag.runtime import Phase4RagRuntime, build_phase4_runtime


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
            self.runtime.close()
        except Exception as error:
            failure = error
        try:
            asyncio.run(self._close_gateway())
        except Exception as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    async def _close_gateway(self) -> None:
        closer = getattr(self.gateway, "aclose", None)
        if not callable(closer):
            raise RuntimeError("gateway close unavailable")
        result = closer()
        if inspect.isawaitable(result):
            await result


def build_configured_phase4_runtime(
    settings: AppSettings = DEFAULT_SETTINGS,
    *,
    data_root: Path | None = None,
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
    gateway = LMStudioGateway(settings, httpx2.AsyncHTTPTransport())
    try:
        runtime = build_phase4_runtime(paths=paths, config=config, gateway=gateway)
    except Exception:
        try:
            asyncio.run(gateway.aclose())
        except Exception as cleanup_error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from cleanup_error
        raise
    return ConfiguredPhase4Runtime(runtime, gateway, runtime.service)

