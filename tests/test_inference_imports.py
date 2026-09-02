"""Regression tests for side-effect-free inference imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_inference_imports_do_not_construct_http_or_create_data(tmp_path: Path) -> None:
    script = """
import httpx2
def forbidden(*args, **kwargs):
    raise AssertionError("HTTP construction during import")
httpx2.AsyncClient = forbidden
httpx2.AsyncHTTPTransport = forbidden
import aaos.config
import aaos.inference
import aaos.inference.models
import aaos.inference.gateway
import aaos.inference.lm_studio
import aaos.inference.lm_studio_lifecycle
import aaos.main
"""
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "data").exists()
