from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

MODULE = "aaos.integrations.google_calendar.client"


def run_fresh_process(source: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]

    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    source_path = str(root / "src")
    environment["PYTHONPATH"] = source_path if not existing else source_path + os.pathsep + existing

    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def assert_probe_passed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == "NO_SIDE_EFFECTS"


def test_import_does_not_read_credentials() -> None:
    result = run_fresh_process(
        f"""
        from pathlib import Path

        original_read_text = Path.read_text
        original_read_bytes = Path.read_bytes

        def forbidden_read_text(self, *args, **kwargs):
            if "credential" in str(self).lower() or "service-account" in str(self).lower():
                raise AssertionError("credential read during import")
            return original_read_text(self, *args, **kwargs)

        def forbidden_read_bytes(self, *args, **kwargs):
            if "credential" in str(self).lower() or "service-account" in str(self).lower():
                raise AssertionError("credential read during import")
            return original_read_bytes(self, *args, **kwargs)

        Path.read_text = forbidden_read_text
        Path.read_bytes = forbidden_read_bytes

        __import__("{MODULE}")
        print("NO_SIDE_EFFECTS")
        """
    )

    assert_probe_passed(result)


def test_import_does_not_open_socket() -> None:
    result = run_fresh_process(
        f"""
        import socket

        def forbidden_connect(self, *args, **kwargs):
            raise AssertionError("socket connection attempted during import")

        socket.socket.connect = forbidden_connect

        __import__("{MODULE}")
        print("NO_SIDE_EFFECTS")
        """
    )

    assert_probe_passed(result)


def test_import_does_not_initialize_google_client() -> None:
    result = run_fresh_process(
        f"""
        import sys
        import types

        googleapiclient = types.ModuleType("googleapiclient")
        discovery = types.ModuleType("googleapiclient.discovery")

        def forbidden_build(*args, **kwargs):
            raise AssertionError("Google client initialized during import")

        discovery.build = forbidden_build
        googleapiclient.discovery = discovery

        sys.modules["googleapiclient"] = googleapiclient
        sys.modules["googleapiclient.discovery"] = discovery

        __import__("{MODULE}")
        print("NO_SIDE_EFFECTS")
        """
    )

    assert_probe_passed(result)


def test_import_does_not_connect_database() -> None:
    result = run_fresh_process(
        f"""
        import sqlite3
        import sqlalchemy

        def forbidden_connect(*args, **kwargs):
            raise AssertionError("database connected during import")

        def forbidden_create_engine(*args, **kwargs):
            raise AssertionError("database engine initialized during import")

        sqlite3.connect = forbidden_connect
        sqlalchemy.create_engine = forbidden_create_engine

        __import__("{MODULE}")
        print("NO_SIDE_EFFECTS")
        """
    )

    assert_probe_passed(result)
