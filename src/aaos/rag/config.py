"""Side-effect-free configuration and identity for the Phase 4 RAG index."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonblank_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{name} must be a non-blank string")
    return normalized


@dataclass(frozen=True, slots=True)
class RagRuntimePaths:
    database_path: Path
    chroma_root: Path
    index_lock_path: Path
    evaluation_output_root: Path


def resolve_rag_runtime_paths(data_root: str | Path) -> RagRuntimePaths:
    """Resolve disposable, confined RAG paths without creating filesystem state."""
    injected_root = Path(data_root)
    if not injected_root.is_absolute():
        raise ValueError("rag data root must be an absolute normalized path")
    root = injected_root.expanduser()
    if root.resolve(strict=False) != root:
        raise ValueError("rag data root must be an absolute normalized path")
    return RagRuntimePaths(
        database_path=root / "database" / "aaos.db",
        chroma_root=root / "rag" / "chroma",
        index_lock_path=root / "temporary" / "phase4-rag-index.lock",
        evaluation_output_root=root / "rag" / "evaluations",
    )


@dataclass(frozen=True, slots=True)
class RagIndexConfig:
    chunk_target_tokens: int
    chunk_preferred_min_tokens: int
    chunk_preferred_max_tokens: int
    chunk_hard_max_tokens: int
    chunk_overlap_tokens: int
    dense_candidate_limit: int
    lexical_candidate_limit: int
    rrf_k: int
    max_evidence_chunks: int
    max_evidence_tokens: int
    chunker_version: str
    lexical_normalizer_version: str
    sufficiency_policy_version: str
    embedding_model_identifier: str
    embedding_model_revision: str
    embedding_dimensions: int

    def __post_init__(self) -> None:
        for name in (
            "chunk_target_tokens",
            "chunk_preferred_min_tokens",
            "chunk_preferred_max_tokens",
            "chunk_hard_max_tokens",
            "chunk_overlap_tokens",
            "dense_candidate_limit",
            "lexical_candidate_limit",
            "rrf_k",
            "max_evidence_chunks",
            "max_evidence_tokens",
            "embedding_dimensions",
        ):
            _positive_int(getattr(self, name), name)
        if self.chunk_preferred_min_tokens > self.chunk_target_tokens:
            raise ValueError("chunk_preferred_min_tokens must not exceed chunk_target_tokens")
        if self.chunk_preferred_max_tokens > self.chunk_hard_max_tokens:
            raise ValueError("chunk_preferred_max_tokens must not exceed chunk_hard_max_tokens")
        if self.chunk_preferred_min_tokens > self.chunk_preferred_max_tokens:
            raise ValueError("chunk_preferred token range is invalid")
        if self.chunk_hard_max_tokens < self.chunk_target_tokens:
            raise ValueError("chunk_hard_max_tokens must be at least chunk_target_tokens")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("chunk_overlap_tokens must be less than chunk_target_tokens")
        for name in (
            "chunker_version",
            "lexical_normalizer_version",
            "sufficiency_policy_version",
            "embedding_model_identifier",
            "embedding_model_revision",
        ):
            object.__setattr__(self, name, _nonblank_text(getattr(self, name), name))


def compute_config_fingerprint(config: RagIndexConfig) -> str:
    """Return the SHA-256 identity of all retrieval-affecting configuration."""
    if not isinstance(config, RagIndexConfig):
        raise ValueError("config must be a RagIndexConfig")
    payload = asdict(config)
    canonical_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical_json.encode("utf-8")).hexdigest()
