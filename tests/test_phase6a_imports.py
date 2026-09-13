from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLD_IMPORT_PROBE = r"""
import builtins
import asyncio
import os
import platform
import socket
import sqlite3
import ssl
import subprocess
import sys

# SQLAlchemy's pure import path asks the standard library for the host
# machine. Resolve that non-AAOS environment fact before installing the
# side-effect guards; the target modules themselves remain cold-imported.
platform.machine()

touched = []
def blocked(*args, **kwargs):
    touched.append("guard")
    raise RuntimeError("side effect during cold import")

socket.socket = blocked
socket.create_connection = blocked
subprocess.Popen = blocked
os.system = blocked
builtins.open = blocked
for name in ("open", "mkdir", "makedirs", "unlink", "remove", "rmdir", "replace"):
    setattr(os, name, blocked)
sqlite3.connect = blocked

for module_name in (
    "aaos.agent.contracts",
    "aaos.agent.policy",
    "aaos.agent.registry",
    "aaos.agent.proposal",
    "aaos.agent.orchestrator",
    "aaos.agent.observations",
    "aaos.agent.events",
    "aaos.approval.contracts",
    "aaos.approval.state",
    "aaos.approval.events",
    "aaos.approval.repository",
    "aaos.approval.service",
    "aaos.approval.adapters",
    "aaos.persistence.approval_models",
):
    __import__(module_name)

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
