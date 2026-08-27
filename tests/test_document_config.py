"""Tests for side-effect-free Phase 3 runtime-path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from aaos.documents.config import DocumentRuntimePaths, resolve_document_runtime_paths


def test_runtime_paths_are_confined_beneath_the_injected_absolute_root(tmp_path: Path) -> None:
    paths = resolve_document_runtime_paths(tmp_path)

    assert paths == DocumentRuntimePaths(
        database_path=tmp_path / "database" / "aaos.db",
        content_store_root=tmp_path / "documents",
        ingestion_lock_path=tmp_path / "temporary" / "phase3-ingestion.lock",
    )


def test_runtime_path_resolution_does_not_create_files_or_directories(tmp_path: Path) -> None:
    data_root = tmp_path / "not-created"

    paths = resolve_document_runtime_paths(data_root)

    assert not data_root.exists()
    assert not paths.database_path.exists()
    assert not paths.content_store_root.exists()
    assert not paths.ingestion_lock_path.exists()


def test_relative_data_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="absolute normalized path"):
        resolve_document_runtime_paths(Path("relative-data"))


def test_non_normalized_absolute_data_root_is_rejected(tmp_path: Path) -> None:
    non_normalized = tmp_path / "child" / ".."

    with pytest.raises(ValueError, match="absolute normalized path"):
        resolve_document_runtime_paths(non_normalized)
