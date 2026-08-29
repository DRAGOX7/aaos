"""Compatibility and privacy guardrails for Phase 4 RAG dependencies.

The production changes that these tests catch are an unpinned/incompatible RAG
distribution, an evaluation package promoted into the runtime dependency group,
or a dependency import that creates AAOS's normal persistent storage.  The
checks intentionally query installed metadata and execute real imports; they
never construct a Chroma client or a BGE-M3 model, so they cannot create an
index or download model weights.

Compatibility decision (verified 2026-08-29 against official metadata):

| Distribution | Version | Group | License | Compatibility and side effects |
| --- | --- | --- | --- | --- |
| chromadb | 1.5.9 | production | Apache-2.0 | Python >=3.9; cp39-abi3 win_amd64 wheel; embedded persistence is opt-in by client construction. |
| FlagEmbedding | 1.4.2 | production | MIT | Python 3.11 resolves with torch/transformers; BGE-M3 support is model construction only and downloads weights only when requested. |
| torch | 2.13.0+cu130 | production | BSD-3-Clause | Official PyTorch cu130 CPython 3.11 Windows wheel; pinned to PyTorch's explicit CUDA index. |
| ragas | 0.4.3 | dev | Apache-2.0 | Python >=3.9, evaluation-only; telemetry is opt-out with RAGAS_DO_NOT_TRACK=true and evaluation must use explicit local adapters. |
| langchain-community | 0.3.27 | dev | MIT | RAGAS 0.4.3 imports its VertexAI adapter; this pin holds the compatible LangChain 0.3 API family rather than resolving the incompatible 0.4 API. |

Authoritative sources:
- https://pypi.org/project/chromadb/1.5.9/
- https://pypi.org/project/FlagEmbedding/1.4.2/
- https://pypi.org/project/ragas/0.4.3/
- https://pypi.org/project/langchain-community/0.3.27/
- https://download.pytorch.org/whl/cu130/torch/
- https://github.com/advisories/GHSA-95ww-475f-pr4f
- https://github.com/chroma-core/chroma/issues/7463
- https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE
- https://github.com/vibrantlabsai/ragas/blob/main/LICENSE
"""

from __future__ import annotations

import ast
import importlib.metadata
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"
NORMAL_DATABASE = PROJECT_ROOT / "data/database/aaos.db"
NORMAL_RAG_ROOT = PROJECT_ROOT / "data/rag"

EXPECTED_CHROMADB_VERSION = "1.5.9"
EXPECTED_FLAGEMBEDDING_VERSION = "1.4.2"
EXPECTED_TORCH_VERSION = "2.13.0+cu130"
EXPECTED_TRANSFORMERS_VERSION = "5.16.1"
EXPECTED_SENTENCE_TRANSFORMERS_VERSION = "6.0.0"
EXPECTED_TOKENIZERS_VERSION = "0.23.1"
EXPECTED_PACKAGING_VERSION = "25.0"
EXPECTED_RAGAS_VERSION = "0.4.3"
EXPECTED_LANGCHAIN_COMMUNITY_VERSION = "0.3.27"
PROHIBITED_DISTRIBUTIONS = ("PyMuPDF", "pytesseract", "easyocr", "ocrmypdf")
PRODUCTION_FORBIDDEN_MODULES = ("ragas", "openai", "langchain_openai")


def _path_snapshot(path: Path) -> tuple[tuple[str, int, int, int], ...] | None:
    if not path.exists():
        return None

    entries = [path, *path.rglob("*")]
    return tuple(
        (
            str(entry.relative_to(path)),
            entry.stat().st_mode,
            entry.stat().st_size,
            entry.stat().st_mtime_ns,
        )
        for entry in entries
    )


def _lock_versions() -> dict[str, str]:
    lock = tomllib.loads(UV_LOCK_PATH.read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in lock["package"]}


def test_phase4_dependency_versions_are_exact() -> None:
    assert importlib.metadata.version("chromadb") == EXPECTED_CHROMADB_VERSION
    assert importlib.metadata.version("FlagEmbedding") == EXPECTED_FLAGEMBEDDING_VERSION
    assert importlib.metadata.version("ragas") == EXPECTED_RAGAS_VERSION
    assert importlib.metadata.version("langchain-community") == EXPECTED_LANGCHAIN_COMMUNITY_VERSION


def test_phase4_production_declarations_and_material_lock_versions_are_exact() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    production_dependencies = pyproject["project"]["dependencies"]
    lock = tomllib.loads(UV_LOCK_PATH.read_text(encoding="utf-8"))
    lock_versions = _lock_versions()
    lock_packages = {package["name"]: package for package in lock["package"]}

    assert f"chromadb=={EXPECTED_CHROMADB_VERSION}" in production_dependencies
    assert f"FlagEmbedding=={EXPECTED_FLAGEMBEDDING_VERSION}" in production_dependencies
    assert f"torch=={EXPECTED_TORCH_VERSION}" in production_dependencies
    assert {
        package: lock_versions[package]
        for package in (
            "chromadb",
            "flagembedding",
            "torch",
            "transformers",
            "sentence-transformers",
            "tokenizers",
            "packaging",
        )
    } == {
        "chromadb": EXPECTED_CHROMADB_VERSION,
        "flagembedding": EXPECTED_FLAGEMBEDDING_VERSION,
        "torch": EXPECTED_TORCH_VERSION,
        "transformers": EXPECTED_TRANSFORMERS_VERSION,
        "sentence-transformers": EXPECTED_SENTENCE_TRANSFORMERS_VERSION,
        "tokenizers": EXPECTED_TOKENIZERS_VERSION,
        "packaging": EXPECTED_PACKAGING_VERSION,
    }
    assert pyproject["tool"]["uv"]["sources"]["torch"] == {"index": "pytorch-cu130"}
    assert {
        "name": "pytorch-cu130",
        "url": "https://download.pytorch.org/whl/cu130",
        "explicit": True,
    } in pyproject["tool"]["uv"]["index"]
    assert lock_packages["torch"]["source"] == {
        "registry": "https://download.pytorch.org/whl/cu130"
    }


def test_ragas_is_dev_only_and_ocr_distributions_are_not_installed() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    production_dependencies = pyproject["project"]["dependencies"]
    dev_dependencies = pyproject["dependency-groups"]["dev"]

    assert not any(dependency.lower().startswith("ragas") for dependency in production_dependencies)
    assert any(dependency == f"ragas=={EXPECTED_RAGAS_VERSION}" for dependency in dev_dependencies)
    assert any(
        dependency == f"langchain-community=={EXPECTED_LANGCHAIN_COMMUNITY_VERSION}"
        for dependency in dev_dependencies
    )
    for prohibited_dependency in ("ragas", "openai", "langchain-openai"):
        assert not any(
            dependency.lower().replace("_", "-").startswith(prohibited_dependency)
            for dependency in production_dependencies
        )

    for distribution in PROHIBITED_DISTRIBUTIONS:
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version(distribution)


def test_production_modules_do_not_import_evaluation_or_cloud_client_modules() -> None:
    for source_path in (PROJECT_ROOT / "src" / "aaos").rglob("*.py"):
        module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imports = (
            imported_name
            for node in ast.walk(module)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for imported_name in (
                (alias.name for alias in node.names)
                if isinstance(node, ast.Import)
                else (() if node.module is None else (node.module,))
            )
        )
        assert all(
            all(
                name != blocked and not name.startswith(f"{blocked}.")
                for blocked in PRODUCTION_FORBIDDEN_MODULES
            )
            for name in imports
        ), source_path


def test_rag_dependency_imports_are_side_effect_safe(tmp_path: Path) -> None:
    normal_state_before = {
        NORMAL_DATABASE: _path_snapshot(NORMAL_DATABASE),
        NORMAL_RAG_ROOT: _path_snapshot(NORMAL_RAG_ROOT),
    }
    cache_root = tmp_path / "isolated-hf-cache"
    script = """
import os
import pathlib
import socket

# Load HTTP/TLS module definitions before replacing connection constructors.
# This keeps the guard focused on connection attempts rather than breaking
# Python's SSL class declarations during a transitive import.
import httpx

def forbidden(*_args, **_kwargs):
    raise AssertionError("network access during dependency import")

socket.socket = forbidden
socket.create_connection = forbidden

import chromadb
import FlagEmbedding
import ragas
from langchain_community.chat_models.vertexai import ChatVertexAI

assert not pathlib.Path("data/database/aaos.db").exists()
assert not pathlib.Path("data/rag").exists()
for cache_variable in (
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "HF_DATASETS_CACHE",
    "XDG_CACHE_HOME",
):
    assert not pathlib.Path(os.environ[cache_variable]).exists(), cache_variable
assert ChatVertexAI is not None
print("IMPORT_SAFE_OFFLINE")
"""
    environment = os.environ.copy()
    environment["RAGAS_DO_NOT_TRACK"] = "true"
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["HF_DATASETS_OFFLINE"] = "1"
    environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
    environment["HF_HOME"] = str(cache_root / "home")
    environment["HF_HUB_CACHE"] = str(cache_root / "hub")
    environment["HUGGINGFACE_HUB_CACHE"] = str(cache_root / "legacy-hub")
    environment["TRANSFORMERS_CACHE"] = str(cache_root / "transformers")
    environment["HF_DATASETS_CACHE"] = str(cache_root / "datasets")
    environment["XDG_CACHE_HOME"] = str(cache_root / "xdg")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "IMPORT_SAFE_OFFLINE\n"
    assert completed.stderr == ""
    assert not cache_root.exists()
    assert {
        NORMAL_DATABASE: _path_snapshot(NORMAL_DATABASE),
        NORMAL_RAG_ROOT: _path_snapshot(NORMAL_RAG_ROOT),
    } == normal_state_before
