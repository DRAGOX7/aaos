from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLD_IMPORT_PROBE = r"""
import builtins
import os
import socket
import subprocess
import sys

touched = []
def blocked(*args, **kwargs):
    touched.append("guard")
    raise RuntimeError("side effect during cold import")

socket.socket = blocked
socket.create_connection = blocked
subprocess.Popen = blocked
os.system = blocked
builtins.open = blocked

import aaos.approval

if touched:
    raise SystemExit("side effect guard was touched")
print("NO_SIDE_EFFECTS")
"""


def fresh_probe_environment(tmp_path: Path) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TMPDIR"] = str(tmp_path)
    return environment


def test_phase6a_cold_import_does_not_load_models_or_touch_persistence(tmp_path: Path) -> None:
    probe = subprocess.run(
        [sys.executable, "-c", COLD_IMPORT_PROBE],
        cwd=REPOSITORY_ROOT,
        env=fresh_probe_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "NO_SIDE_EFFECTS"
