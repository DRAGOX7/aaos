"""Task 20 import-side-effect regression tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_rag_runtime_import_has_no_storage_model_or_network_side_effects(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = """
import pathlib
import socket

socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network import'))

import aaos.rag.runtime

assert not pathlib.Path('data/database/aaos.db').exists()
assert not pathlib.Path('data/rag').exists()
assert not pathlib.Path('data/documents').exists()
print('IMPORT_SAFE')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "IMPORT_SAFE\n"
    assert result.stderr == ""


def test_runtime_does_not_import_phase3_or_expose_browser_surface() -> None:
    import aaos.rag.runtime as runtime

    names = set(runtime.__all__)
    assert "Phase4RagRuntime" in names
    assert "build_phase4_runtime" in names
    assert not any("moodle" in name.casefold() for name in names)
    assert not hasattr(runtime, "Page")
    assert not hasattr(runtime, "BrowserContext")
