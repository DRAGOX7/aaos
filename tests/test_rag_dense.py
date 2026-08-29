"""Tests for the deterministic in-memory dense-index fake."""

from __future__ import annotations

from math import nan

import pytest

from aaos.rag.contracts import DenseIndex, DenseSearchHit, DenseVectorRecord, RagGenerationIdentity
from aaos.rag.dense import InMemoryDenseIndexFactory


def _generation(*, generation_id: int = 1, dimensions: int = 2) -> RagGenerationIdentity:
    return RagGenerationIdentity(generation_id, "a" * 64, 0, dimensions)


def test_factory_provides_generation_scoped_dense_index_protocol() -> None:
    factory = InMemoryDenseIndexFactory()
    index = factory.open(_generation())
    typed_index: DenseIndex = index
    record_a = DenseVectorRecord("chunk-a", 7, (0.0, 0.0))
    record_b = DenseVectorRecord("chunk-b", 7, (1.0, 0.0))
    record_other_version = DenseVectorRecord("chunk-c", 8, (0.0, 1.0))

    typed_index.upsert((record_a, record_b, record_other_version))

    hit_a = DenseSearchHit("chunk-a", 1, 0.0)
    hit_b = DenseSearchHit("chunk-b", 2, 1.0)
    assert index.count() == 3
    assert index.contains(("chunk-a", "chunk-c"))
    assert not index.contains(("chunk-a", "missing"))
    assert index.search((0.0, 0.0), limit=2, allowed_document_version_ids=(7,)) == (
        hit_a,
        hit_b,
    )


def test_search_filters_versions_and_breaks_distance_ties_by_chunk_key() -> None:
    index = InMemoryDenseIndexFactory().open(_generation())
    index.upsert(
        (
            DenseVectorRecord("chunk-z", 7, (1.0, 0.0)),
            DenseVectorRecord("chunk-a", 7, (-1.0, 0.0)),
            DenseVectorRecord("chunk-hidden", 8, (0.0, 0.0)),
        )
    )

    assert index.search((0.0, 0.0), limit=2, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk-a", 1, 1.0),
        DenseSearchHit("chunk-z", 2, 1.0),
    )


def test_rejects_wrong_dimension_and_nonfinite_query_vector() -> None:
    index = InMemoryDenseIndexFactory().open(_generation())

    with pytest.raises(ValueError, match="dimensions"):
        index.upsert((DenseVectorRecord("chunk", 7, (0.0,)),))
    with pytest.raises(ValueError, match="vector"):
        index.search((nan, 0.0), limit=1, allowed_document_version_ids=(7,))


def test_rejects_duplicate_chunk_key_within_one_upsert() -> None:
    index = InMemoryDenseIndexFactory().open(_generation())

    with pytest.raises(ValueError, match="duplicate chunk_key"):
        index.upsert(
            (
                DenseVectorRecord("chunk", 7, (0.0, 0.0)),
                DenseVectorRecord("chunk", 8, (1.0, 1.0)),
            )
        )


def test_close_is_idempotent_and_operations_fail_safely_after_close() -> None:
    index = InMemoryDenseIndexFactory().open(_generation())

    index.close()
    index.close()

    with pytest.raises(RuntimeError, match="closed"):
        index.count()
