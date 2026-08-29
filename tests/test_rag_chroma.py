"""Disposable integration tests for the embedded Chroma dense-index adapter."""

from __future__ import annotations

import importlib
import socket
from pathlib import Path

import pytest

from aaos.rag.chroma import ChromaDenseIndexFactory
from aaos.rag.config import resolve_rag_runtime_paths
from aaos.rag.contracts import (
    DenseIndex,
    DenseIndexFactory,
    DenseSearchHit,
    DenseVectorRecord,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
)


def _generation(*, generation_id: int = 1, dimensions: int = 2) -> RagGenerationIdentity:
    return RagGenerationIdentity(generation_id, "a" * 64, 0, dimensions)


def _factory(tmp_path: Path) -> ChromaDenseIndexFactory:
    return ChromaDenseIndexFactory(resolve_rag_runtime_paths(tmp_path))


def _records() -> tuple[DenseVectorRecord, ...]:
    return (
        DenseVectorRecord("chunk-a", 7, (0.0, 0.0)),
        DenseVectorRecord("chunk-b", 7, (1.0, 0.0)),
        DenseVectorRecord("chunk-hidden", 8, (0.0, 1.0)),
    )


def test_factory_provides_generation_scoped_dense_index_protocol(tmp_path: Path) -> None:
    factory: DenseIndexFactory = _factory(tmp_path)
    index: DenseIndex = factory.open(_generation())
    index.upsert(_records())

    assert index.count() == 3
    assert index.contains(("chunk-a", "chunk-hidden"))
    assert not index.contains(("chunk-a", "missing"))
    assert index.search((0.0, 0.0), limit=2, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk-a", 1, 0.0),
        DenseSearchHit("chunk-b", 2, 1.0),
    )


def test_upsert_is_idempotent_and_replaces_an_existing_vector(tmp_path: Path) -> None:
    index = _factory(tmp_path).open(_generation())
    initial = DenseVectorRecord("chunk", 7, (0.0, 0.0))

    index.upsert((initial,))
    index.upsert((initial,))
    index.upsert((DenseVectorRecord("chunk", 7, (2.0, 0.0)),))

    assert index.count() == 1
    assert index.search((2.0, 0.0), limit=1, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk", 1, 0.0),
    )


def test_search_fails_closed_for_empty_allow_list_and_filters_exact_versions(
    tmp_path: Path,
) -> None:
    index = _factory(tmp_path).open(_generation())
    index.upsert(_records())

    assert index.search((0.0, 0.0), limit=3, allowed_document_version_ids=()) == ()
    assert index.search((0.0, 0.0), limit=3, allowed_document_version_ids=(8,)) == (
        DenseSearchHit("chunk-hidden", 1, 1.0),
    )


def test_rejects_invalid_vectors_and_duplicate_keys_before_backend_write(tmp_path: Path) -> None:
    index = _factory(tmp_path).open(_generation())

    with pytest.raises(ValueError, match="dimensions"):
        index.upsert((DenseVectorRecord("chunk", 7, (0.0,)),))
    with pytest.raises(ValueError, match="duplicate chunk_key"):
        index.upsert(
            (
                DenseVectorRecord("chunk", 7, (0.0, 0.0)),
                DenseVectorRecord("chunk", 8, (1.0, 1.0)),
            )
        )
    with pytest.raises(ValueError, match="positive"):
        index.search((0.0, 0.0), limit=0, allowed_document_version_ids=(7,))


def test_generations_are_isolated_and_remove_invalidates_only_target_handles(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    generation_a = _generation(generation_id=1)
    generation_b = _generation(generation_id=2)
    index_a = factory.open(generation_a)
    index_b = factory.open(generation_b)
    index_a.upsert((DenseVectorRecord("chunk-a", 7, (0.0, 0.0)),))
    index_b.upsert((DenseVectorRecord("chunk-b", 7, (0.0, 0.0)),))

    assert index_a.contains(("chunk-a",))
    assert not index_a.contains(("chunk-b",))
    assert index_b.contains(("chunk-b",))
    assert not index_b.contains(("chunk-a",))

    factory.remove(generation_b)

    assert index_a.count() == 1
    with pytest.raises(RagOperationalError) as error:
        index_b.count()
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert factory.open(generation_b).count() == 0


def test_close_is_idempotent_and_reopen_persists_across_a_new_factory(tmp_path: Path) -> None:
    generation = _generation()
    index = _factory(tmp_path).open(generation)
    index.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),))
    index.close()
    index.close()

    with pytest.raises(RagOperationalError) as error:
        index.count()
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE

    reopened = _factory(tmp_path).open(generation)
    assert reopened.count() == 1
    assert reopened.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk", 1, 0.0),
    )


def test_collection_uses_no_default_embedding_function_and_squared_l2(tmp_path: Path) -> None:
    index = _factory(tmp_path).open(_generation())
    collection = index._collection

    assert collection.configuration["hnsw"]["space"] == "l2"
    assert collection._embedding_function is None


def test_embedded_operations_do_not_open_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _blocked_socket(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    index = _factory(tmp_path).open(_generation())
    index.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),))
    assert index.count() == 1


def test_import_has_no_filesystem_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unexpected"

    def _forbidden_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise AssertionError("module import must not create directories")

    monkeypatch.setattr(Path, "mkdir", _forbidden_mkdir)
    importlib.reload(importlib.import_module("aaos.rag.chroma"))

    assert not target.exists()


def test_backend_write_failure_has_only_safe_error_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _factory(tmp_path).open(_generation())

    def _broken_upsert(**kwargs: object) -> None:
        raise RuntimeError(f"secret path: {tmp_path}")

    monkeypatch.setattr(index._collection, "upsert", _broken_upsert)

    with pytest.raises(RagOperationalError) as error:
        index.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),))
    assert error.value.code is RagErrorCode.DENSE_INDEX_WRITE_FAILED
    assert str(tmp_path) not in str(error.value)
    assert str(error.value) == "rag operation failed"
