"""RED tests for deterministic, no-real-write Phase 7E acceptance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_phase7e_acceptance_does_not_import_real_provider_or_model_modules() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    tests_root = Path(__file__).resolve().parent
    script = """
import sys
sys.path.insert(0, SOURCE_ROOT)
import aaos.tasks.execution
import aaos.web.contracts
forbidden = {"google.auth", "googleapiclient.discovery", "lmstudio", "transformers"}
assert forbidden.isdisjoint(sys.modules)
""".replace("SOURCE_ROOT", repr(str(source_root))).replace("TESTS_ROOT", repr(str(tests_root)))
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=tests_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
