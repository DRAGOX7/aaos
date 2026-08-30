"""Disposable integration tests for the embedded Chroma dense-index adapter."""

from __future__ import annotations

import importlib
import os
import socket
import subprocess
import sys
from collections.abc import Callable
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


def _generation(
    *, generation_id: int = 1, revision: int = 0, dimensions: int = 2
) -> RagGenerationIdentity:
    return RagGenerationIdentity(generation_id, "a" * 64, revision, dimensions)


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


def test_persistent_generation_survives_content_revision_publication(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    revision_zero = _generation(revision=0)
    revision_one = _generation(revision=1)
    revision_two = _generation(revision=2)
    factory.open(revision_zero).upsert((DenseVectorRecord("chunk-a", 7, (0.0, 0.0)),))

    assert factory.open(revision_one).contains(("chunk-a",))
    factory.open(revision_one).upsert((DenseVectorRecord("chunk-b", 8, (1.0, 0.0)),))
    assert _factory(tmp_path).open(revision_two).contains(("chunk-a", "chunk-b"))

    factory.remove(revision_two)
    assert _factory(tmp_path).open(revision_zero).count() == 0


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


def test_search_sorts_equal_distances_by_chunk_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _factory(tmp_path).open(_generation())

    monkeypatch.setattr(
        index._collection,
        "query",
        lambda **kwargs: {
            "ids": [["chunk-z", "chunk-a"]],
            "distances": [[4.0, 4.0]],
            "metadatas": [[{"document_version_id": 7}, {"document_version_id": 7}]],
        },
    )

    assert index.search((0.0, 0.0), limit=2, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk-a", 1, 4.0),
        DenseSearchHit("chunk-z", 2, 4.0),
    )


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"document_version_id": 0},
        {"document_version_id": "7"},
        {"document_version_id": 8},
    ],
)
def test_search_rejects_malformed_or_foreign_backend_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: object
) -> None:
    index = _factory(tmp_path).open(_generation())

    monkeypatch.setattr(
        index._collection,
        "query",
        lambda **kwargs: {
            "ids": [["chunk"]],
            "distances": [[0.0]],
            "metadatas": [[metadata]],
        },
    )

    with pytest.raises(RagOperationalError) as error:
        index.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,))
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE


def test_search_rejects_malformed_backend_result_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _factory(tmp_path).open(_generation())

    monkeypatch.setattr(
        index._collection,
        "query",
        lambda **kwargs: {"ids": [["chunk"]], "distances": [[]], "metadatas": [[{}]]},
    )

    with pytest.raises(RagOperationalError) as error:
        index.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,))
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE


def test_collection_reports_squared_l2_distance_for_non_unit_delta(tmp_path: Path) -> None:
    index = _factory(tmp_path).open(_generation())
    index.upsert((DenseVectorRecord("chunk", 7, (2.0, 0.0)),))

    assert index.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,)) == (
        DenseSearchHit("chunk", 1, 4.0),
    )


def test_embedding_dimensions_are_part_of_generation_storage_identity(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    generation_2d = _generation(dimensions=2)
    generation_3d = _generation(dimensions=3)
    index_2d = factory.open(generation_2d)
    index_3d = factory.open(generation_3d)
    index_2d.upsert((DenseVectorRecord("chunk-2d", 7, (0.0, 0.0)),))
    index_3d.upsert((DenseVectorRecord("chunk-3d", 7, (0.0, 0.0, 0.0)),))

    assert index_2d.contains(("chunk-2d",))
    assert not index_2d.contains(("chunk-3d",))
    assert index_3d.contains(("chunk-3d",))
    factory.remove(generation_2d)
    assert index_3d.count() == 1


def test_fresh_process_reopens_persistent_generation_without_network(tmp_path: Path) -> None:
    generation = _generation()
    index = _factory(tmp_path).open(generation)
    index.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),))
    index.close()
    child_code = """
from pathlib import Path
from aaos.rag.chroma import ChromaDenseIndexFactory
from aaos.rag.config import resolve_rag_runtime_paths
from aaos.rag.contracts import RagGenerationIdentity
import socket
socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network access'))
root = Path(__import__('sys').argv[1])
generation = RagGenerationIdentity(1, 'a' * 64, 0, 2)
index = ChromaDenseIndexFactory(resolve_rag_runtime_paths(root)).open(generation)
assert index.count() == 1
"""
    environment = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}

    result = subprocess.run(
        [sys.executable, "-c", child_code, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_import_has_no_client_socket_or_filesystem_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    normal_database = Path.cwd() / "data" / "database" / "aaos.db"
    normal_rag = Path.cwd() / "data" / "rag"
    injected_root = tmp_path / "injected"
    assert not normal_database.exists()
    assert not normal_rag.exists()
    assert not injected_root.exists()

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("import side effect")

    monkeypatch.setattr("chromadb.PersistentClient", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(Path, "mkdir", _forbidden)
    sys.modules.pop("aaos.rag.chroma", None)
    importlib.import_module("aaos.rag.chroma")

    assert not normal_database.exists()
    assert not normal_rag.exists()
    assert not injected_root.exists()


@pytest.mark.parametrize("method_name", ["query", "count", "get"])
def test_backend_read_failures_are_safely_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    index = _factory(tmp_path).open(_generation())

    def _broken(**kwargs: object) -> None:
        raise RuntimeError(f"secret path: {tmp_path}")

    monkeypatch.setattr(index._collection, method_name, _broken)
    operation: Callable[[], object] = {
        "query": lambda: index.search((0.0, 0.0), limit=1, allowed_document_version_ids=(7,)),
        "count": index.count,
        "get": lambda: index.contains(("chunk",)),
    }[method_name]

    with pytest.raises(RagOperationalError) as error:
        operation()
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert str(tmp_path) not in str(error.value)


def test_factory_open_and_remove_failures_are_safely_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(tmp_path)
    generation = _generation()

    def _broken_client(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"secret path: {tmp_path}")

    monkeypatch.setattr("aaos.rag.chroma.chromadb.PersistentClient", _broken_client)
    with pytest.raises(RagOperationalError) as open_error:
        factory.open(generation)
    assert open_error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE

    monkeypatch.undo()
    factory.open(generation)
    monkeypatch.setattr("aaos.rag.chroma.chromadb.PersistentClient", _broken_client)
    with pytest.raises(RagOperationalError) as remove_error:
        factory.remove(generation)
    assert remove_error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE


def test_remove_from_another_factory_invalidates_old_handle_when_chroma_rejects_it(
    tmp_path: Path,
) -> None:
    generation = _generation()
    first_factory = _factory(tmp_path)
    old_handle = first_factory.open(generation)
    old_handle.upsert((DenseVectorRecord("chunk", 7, (0.0, 0.0)),))

    _factory(tmp_path).remove(generation)

    with pytest.raises(RagOperationalError) as error:
        old_handle.count()
    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
