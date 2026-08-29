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
| ragas | 0.4.3 | dev | Apache-2.0 | Python >=3.9, evaluation-only; telemetry is opt-out with RAGAS_DO_NOT_TRACK=true and evaluation must use explicit local adapters. |
| langchain-community | 0.3.27 | dev | MIT | RAGAS 0.4.3 imports its VertexAI adapter; this pin holds the compatible LangChain 0.3 API family rather than resolving the incompatible 0.4 API. |

Authoritative sources:
- https://pypi.org/project/chromadb/1.5.9/
- https://pypi.org/project/FlagEmbedding/1.4.2/
- https://pypi.org/project/ragas/0.4.3/
- https://pypi.org/project/langchain-community/0.3.27/
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
NORMAL_DATABASE = PROJECT_ROOT / "data/database/aaos.db"
NORMAL_RAG_ROOT = PROJECT_ROOT / "data/rag"

EXPECTED_CHROMADB_VERSION = "1.5.9"
EXPECTED_FLAGEMBEDDING_VERSION = "1.4.2"
EXPECTED_RAGAS_VERSION = "0.4.3"
EXPECTED_LANGCHAIN_COMMUNITY_VERSION = "0.3.27"
PROHIBITED_DISTRIBUTIONS = ("PyMuPDF", "pytesseract", "easyocr", "ocrmypdf")


def test_phase4_dependency_versions_are_exact() -> None:
    assert importlib.metadata.version("chromadb") == EXPECTED_CHROMADB_VERSION
    assert importlib.metadata.version("FlagEmbedding") == EXPECTED_FLAGEMBEDDING_VERSION
    assert importlib.metadata.version("ragas") == EXPECTED_RAGAS_VERSION
    assert importlib.metadata.version("langchain-community") == EXPECTED_LANGCHAIN_COMMUNITY_VERSION


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

    for distribution in PROHIBITED_DISTRIBUTIONS:
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version(distribution)


def test_production_modules_do_not_import_dev_only_ragas() -> None:
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
        assert all(name != "ragas" and not name.startswith("ragas.") for name in imports), (
            source_path
        )


def test_rag_dependency_imports_are_side_effect_safe(tmp_path: Path) -> None:
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_RAG_ROOT.exists()
    script = """
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

assert not pathlib.Path("data/database/aaos.db").exists()
assert not pathlib.Path("data/rag").exists()
print("IMPORT_SAFE")
"""
    environment = os.environ.copy()
    environment["RAGAS_DO_NOT_TRACK"] = "true"
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
    assert completed.stdout == "IMPORT_SAFE\n"
    assert completed.stderr == ""
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_RAG_ROOT.exists()
