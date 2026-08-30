"""Tests for the deterministic in-memory dense-index fake."""

from __future__ import annotations

from math import nan

import pytest

from aaos.rag.contracts import (
    DenseIndex,
    DenseIndexFactory,
    DenseSearchHit,
    DenseVectorRecord,
    RagGenerationIdentity,
)
from aaos.rag.dense import InMemoryDenseIndexFactory


def _generation(
    *, generation_id: int = 1, revision: int = 0, dimensions: int = 2
) -> RagGenerationIdentity:
    return RagGenerationIdentity(generation_id, "a" * 64, revision, dimensions)


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

    operations = (
        lambda: index.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),)),
        lambda: index.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,)),
        lambda: index.contains(("chunk",)),
        index.count,
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match="closed"):
            operation()


def test_factory_is_generation_isolated_and_remove_reopens_empty_index() -> None:
    factory: DenseIndexFactory = InMemoryDenseIndexFactory()
    generation_a = _generation(generation_id=1)
    generation_b = _generation(generation_id=2)
    index_a = factory.open(generation_a)
    index_a.upsert((DenseVectorRecord("chunk-a", 7, (0.0, 0.0)),))

    assert factory.open(generation_b).count() == 0

    factory.remove(generation_a)

    with pytest.raises(RuntimeError, match="closed"):
        index_a.count()
    assert factory.open(generation_a).count() == 0


def test_generation_vectors_remain_visible_across_content_revisions() -> None:
    factory = InMemoryDenseIndexFactory()
    revision_zero = _generation(revision=0)
    revision_one = _generation(revision=1)
    revision_two = _generation(revision=2)
    factory.open(revision_zero).upsert((DenseVectorRecord("chunk-a", 7, (0.0, 0.0)),))

    assert factory.open(revision_one).contains(("chunk-a",))
    factory.open(revision_one).upsert((DenseVectorRecord("chunk-b", 8, (1.0, 0.0)),))
    assert factory.open(revision_two).contains(("chunk-a", "chunk-b"))

    factory.remove(revision_two)
    assert factory.open(revision_zero).count() == 0


def test_rejects_nonfinite_stored_record_vector_independently_of_query_validation() -> None:
    malformed_record = object.__new__(DenseVectorRecord)
    object.__setattr__(malformed_record, "chunk_key", "chunk")
    object.__setattr__(malformed_record, "document_version_id", 7)
    object.__setattr__(malformed_record, "vector", (nan, 0.0))
    index = InMemoryDenseIndexFactory().open(_generation())

    with pytest.raises(ValueError, match="vector"):
        index.upsert((malformed_record,))
