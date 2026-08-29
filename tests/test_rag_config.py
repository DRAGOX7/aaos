"""Tests for side-effect-free Phase 4 RAG configuration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from aaos.rag.config import (
    RagIndexConfig,
    RagRuntimePaths,
    compute_config_fingerprint,
    resolve_rag_runtime_paths,
)


def make_config(**changes: object) -> RagIndexConfig:
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
        "embedding_model_identifier": "BAAI/bge-m3",
        "embedding_model_revision": "v1",
        "embedding_dimensions": 1024,
    }
    values.update(changes)
    return RagIndexConfig(**values)  # type: ignore[arg-type]


def test_runtime_paths_are_confined_beneath_injected_absolute_root(tmp_path: Path) -> None:
    paths = resolve_rag_runtime_paths(tmp_path)

    assert paths == RagRuntimePaths(
        database_path=tmp_path / "database" / "aaos.db",
        chroma_root=tmp_path / "rag" / "chroma",
        index_lock_path=tmp_path / "temporary" / "phase4-rag-index.lock",
        evaluation_output_root=tmp_path / "rag" / "evaluations",
    )


def test_runtime_paths_are_side_effect_free_and_reject_path_escapes(tmp_path: Path) -> None:
    data_root = tmp_path / "not-created"

    assert not data_root.exists()
    resolve_rag_runtime_paths(data_root)
    assert not data_root.exists()

    with pytest.raises(ValueError, match="absolute normalized path"):
        resolve_rag_runtime_paths(Path("relative-data"))
    with pytest.raises(ValueError, match="absolute normalized path"):
        resolve_rag_runtime_paths(tmp_path / "child" / "..")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"chunk_target_tokens": 0}, "chunk_target_tokens"),
        ({"chunk_preferred_min_tokens": 401}, "chunk_preferred"),
        ({"chunk_preferred_max_tokens": 801}, "chunk_preferred"),
        ({"chunk_hard_max_tokens": 399}, "chunk_hard_max_tokens"),
        ({"chunk_overlap_tokens": 400}, "chunk_overlap_tokens"),
        ({"dense_candidate_limit": 0}, "dense_candidate_limit"),
        ({"lexical_candidate_limit": 0}, "lexical_candidate_limit"),
        ({"rrf_k": 0}, "rrf_k"),
        ({"max_evidence_chunks": 0}, "max_evidence_chunks"),
        ({"max_evidence_tokens": 0}, "max_evidence_tokens"),
        ({"chunker_version": " "}, "chunker_version"),
        ({"embedding_dimensions": 0}, "embedding_dimensions"),
    ],
)
def test_invalid_index_configuration_is_rejected(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_config(**changes)


def test_equivalent_configurations_have_the_same_canonical_fingerprint() -> None:
    config_a = make_config()
    config_b = make_config()

    assert compute_config_fingerprint(config_a) == compute_config_fingerprint(config_b)
    assert len(compute_config_fingerprint(config_a)) == 64


@pytest.mark.parametrize(
    "field",
    [
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
        "chunker_version",
        "lexical_normalizer_version",
        "sufficiency_policy_version",
        "embedding_model_identifier",
        "embedding_model_revision",
        "embedding_dimensions",
    ],
)
def test_every_retrieval_affecting_configuration_change_alters_fingerprint(field: str) -> None:
    config = make_config()
    value = getattr(config, field)
    changed_value = value + 1 if isinstance(value, int) else f"{value}-changed"

    assert compute_config_fingerprint(config) != compute_config_fingerprint(
        replace(config, **cast(Any, {field: changed_value}))
    )
