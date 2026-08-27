"""Child-process import-safety checks for Phase 3 document modules."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMAL_DATABASE = PROJECT_ROOT / "data/database/aaos.db"
NORMAL_DOCUMENTS = PROJECT_ROOT / "data/documents"


def test_phase3_imports_have_no_runtime_or_normal_path_side_effects(tmp_path: Path) -> None:
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_DOCUMENTS.exists()
    script = """
import pathlib
import socket
import subprocess

from alembic import command
import aaos.persistence.database as database
import playwright.sync_api

def blocked(*_args, **_kwargs):
    raise AssertionError("import attempted a forbidden side effect")

socket.socket = blocked
socket.create_connection = blocked
subprocess.Popen = blocked
command.upgrade = blocked
database.create_database_engine = blocked

import aaos.documents
import aaos.documents.cli
import aaos.moodle.file_download

assert not pathlib.Path("data/database/aaos.db").exists()
assert not pathlib.Path("data/documents").exists()
assert not pathlib.Path("data/temporary/phase3-ingestion.lock").exists()
print("IMPORT_SAFE")
"""
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    source_path = str(PROJECT_ROOT / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else os.pathsep.join((source_path, existing_pythonpath))
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "IMPORT_SAFE\n"
    assert completed.stderr == ""
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_DOCUMENTS.exists()
