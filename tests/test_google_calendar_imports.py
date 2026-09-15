from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

MODULE = "aaos.integrations.google_calendar.client"

PHASE6B_MODULES = (
    "aaos.integrations.google_calendar",
    "aaos.integrations.google_calendar.contracts",
    "aaos.integrations.google_calendar.canonicalization",
    "aaos.integrations.google_calendar.errors",
    "aaos.integrations.google_calendar.client",
    "aaos.integrations.google_calendar.adapter",
)


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


@pytest.mark.parametrize("module_name", PHASE6B_MODULES)
def test_every_phase6b_module_import_is_side_effect_free(
    module_name: str,
) -> None:
    result = run_fresh_process(
        f"""
        import builtins
        import importlib
        import io
        import os
        import socket
        import sqlite3
        import subprocess
        import sys
        from pathlib import Path

        import sqlalchemy
        import google.oauth2.service_account as service_account
        import googleapiclient.discovery as discovery

        # Phase 6A is frozen and not under this Task 5 import-side-effect probe.
        # Preload its contracts/adapters before installing the Phase 6B guards.
        import aaos.approval.adapters
        import aaos.approval.contracts

        synthetic_fork_names = []
        def synthetic_fork_probe(*args, **kwargs):
            del args, kwargs
            return 0

        for fork_name in ("fork", "forkpty", "vfork"):
            if not hasattr(os, fork_name):
                setattr(os, fork_name, synthetic_fork_probe)
                synthetic_fork_names.append(fork_name)

        original_fork_style_launchers = tuple(
            (fork_name, getattr(os, fork_name))
            for fork_name in ("fork", "forkpty", "vfork")
        )

        original_socket_connect_ex = socket.socket.connect_ex
        original_io_open = io.open
        original_os_open = os.open
        original_os_process_launchers = tuple(
            (process_name, getattr(os, process_name))
            for process_name in (
                "system",
                "popen",
                "startfile",
                "spawnl",
                "spawnle",
                "spawnlp",
                "spawnlpe",
                "spawnv",
                "spawnve",
                "spawnvp",
                "spawnvpe",
                "posix_spawn",
                "posix_spawnp",
                "execl",
                "execle",
                "execlp",
                "execlpe",
                "execv",
                "execve",
                "execvp",
                "execvpe",
            )
            if hasattr(os, process_name)
        )
        original_from_service_account_file = (
            service_account.Credentials.from_service_account_file.__func__
        )
        original_build_from_document = discovery.build_from_document

        prefix = "aaos.integrations.google_calendar"
        for loaded_name in tuple(sys.modules):
            if loaded_name == prefix or loaded_name.startswith(prefix + "."):
                del sys.modules[loaded_name]

        counters = {{
            "file_open": 0,
            "socket": 0,
            "subprocess": 0,
            "database": 0,
            "credential_parse": 0,
            "sdk_build": 0,
            "model_rag": 0,
        }}

        def fail(name, message):
            counters[name] += 1
            raise AssertionError(message)

        def forbidden_open(*args, **kwargs):
            del args, kwargs
            return fail("file_open", "file opened during Phase 6B import")

        def forbidden_path_open(self, *args, **kwargs):
            del self, args, kwargs
            return fail("file_open", "Path.open used during Phase 6B import")

        def forbidden_read_bytes(self, *args, **kwargs):
            del self, args, kwargs
            return fail("file_open", "Path.read_bytes used during Phase 6B import")

        def forbidden_read_text(self, *args, **kwargs):
            del self, args, kwargs
            return fail("file_open", "Path.read_text used during Phase 6B import")

        def forbidden_io_open(*args, **kwargs):
            del args, kwargs
            return fail("file_open", "io.open used during Phase 6B import")

        def forbidden_os_open(*args, **kwargs):
            del args, kwargs
            return fail("file_open", "os.open used during Phase 6B import")

        def forbidden_socket_connect(self, *args, **kwargs):
            del self, args, kwargs
            return fail("socket", "socket connection attempted during Phase 6B import")

        def forbidden_socket_connect_ex(self, *args, **kwargs):
            del self, args, kwargs
            return fail("socket", "socket connect_ex attempted during Phase 6B import")

        def forbidden_create_connection(*args, **kwargs):
            del args, kwargs
            return fail("socket", "socket.create_connection used during Phase 6B import")

        def forbidden_popen(*args, **kwargs):
            del args, kwargs
            return fail("subprocess", "subprocess created during Phase 6B import")

        def forbidden_os_process_launch(*args, **kwargs):
            del args, kwargs
            return fail("subprocess", "OS process launch attempted during Phase 6B import")

        def forbidden_sqlite_connect(*args, **kwargs):
            del args, kwargs
            return fail("database", "sqlite database opened during Phase 6B import")

        def forbidden_create_engine(*args, **kwargs):
            del args, kwargs
            return fail("database", "SQLAlchemy engine created during Phase 6B import")

        def forbidden_credential_parse(cls, *args, **kwargs):
            del cls, args, kwargs
            return fail("credential_parse", "credential parsed during Phase 6B import")

        def forbidden_sdk_build(*args, **kwargs):
            del args, kwargs
            return fail("sdk_build", "Google SDK constructed during Phase 6B import")

        original_import = builtins.__import__

        blocked_model_prefixes = (
            "aaos.rag",
            "torch",
            "transformers",
            "sentence_transformers",
            "FlagEmbedding",
            "llama_cpp",
        )

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith(blocked_model_prefixes):
                counters["model_rag"] += 1
                raise AssertionError("model/RAG import attempted during Phase 6B import")
            return original_import(name, globals, locals, fromlist, level)

        builtins.open = forbidden_open
        Path.open = forbidden_path_open
        Path.read_bytes = forbidden_read_bytes
        Path.read_text = forbidden_read_text
        io.open = forbidden_io_open
        os.open = forbidden_os_open
        socket.socket.connect = forbidden_socket_connect
        socket.socket.connect_ex = forbidden_socket_connect_ex
        socket.create_connection = forbidden_create_connection
        subprocess.Popen = forbidden_popen
        for process_name, _ in original_os_process_launchers:
            setattr(os, process_name, forbidden_os_process_launch)
        for fork_name, _ in original_fork_style_launchers:
            setattr(os, fork_name, forbidden_os_process_launch)
        sqlite3.connect = forbidden_sqlite_connect
        sqlalchemy.create_engine = forbidden_create_engine
        service_account.Credentials.from_service_account_info = classmethod(
            forbidden_credential_parse
        )
        service_account.Credentials.from_service_account_file = classmethod(
            forbidden_credential_parse
        )
        discovery.build = forbidden_sdk_build
        discovery.build_from_document = forbidden_sdk_build
        builtins.__import__ = guarded_import

        # Harness-integrity probes. Each alternate capability path called out by
        # security review must actually trip its counter before the real import.
        assert socket.socket.connect_ex is not original_socket_connect_ex
        assert io.open is not original_io_open
        assert os.open is not original_os_open
        assert (
            service_account.Credentials.from_service_account_file.__func__
            is not original_from_service_account_file
        )
        assert discovery.build_from_document is not original_build_from_document

        for process_name, original_process_launcher in original_os_process_launchers:
            assert getattr(os, process_name) is not original_process_launcher

        for fork_name, original_fork_launcher in original_fork_style_launchers:
            assert getattr(os, fork_name) is not original_fork_launcher

        def assert_guard_trips(counter_name, callback):
            before = counters[counter_name]
            try:
                callback()
            except AssertionError:
                pass
            else:
                raise AssertionError(f"{{counter_name}} guard did not trip")

            assert counters[counter_name] == before + 1

        for process_name, _ in original_os_process_launchers:
            assert_guard_trips(
                "subprocess",
                lambda process_name=process_name: getattr(os, process_name)(),
            )

        for fork_name, _ in original_fork_style_launchers:
            assert_guard_trips(
                "subprocess",
                lambda fork_name=fork_name: getattr(os, fork_name)(),
            )

        # Windows does not expose fork-style APIs. Synthetic names were injected
        # only to prove the harness would guard them; remove those names before
        # the real Phase 6B import so the subprocess sees the native platform API.
        for fork_name in synthetic_fork_names:
            delattr(os, fork_name)

        assert_guard_trips(
            "socket",
            lambda: socket.socket.connect_ex(object(), ("127.0.0.1", 9)),
        )
        assert_guard_trips(
            "file_open",
            lambda: io.open("AAOS_TASK5_IMPORT_PROBE", "rb"),
        )
        assert_guard_trips(
            "file_open",
            lambda: os.open("AAOS_TASK5_IMPORT_PROBE", os.O_RDONLY),
        )
        assert_guard_trips(
            "credential_parse",
            lambda: service_account.Credentials.from_service_account_file(
                "AAOS_TASK5_IMPORT_PROBE.json"
            ),
        )
        assert_guard_trips(
            "sdk_build",
            lambda: discovery.build_from_document("{{}}"),
        )

        # Self-probes above intentionally increment counters. Reset them so the
        # assertions below describe only the Phase 6B module import itself.
        for counter_name in counters:
            counters[counter_name] = 0

        baseline_modules = set(sys.modules)

        importlib.import_module("{module_name}")

        new_modules = set(sys.modules) - baseline_modules
        forbidden_new_modules = sorted(
            name
            for name in new_modules
            if name.startswith(blocked_model_prefixes)
        )

        assert forbidden_new_modules == []
        assert counters == {{
            "file_open": 0,
            "socket": 0,
            "subprocess": 0,
            "database": 0,
            "credential_parse": 0,
            "sdk_build": 0,
            "model_rag": 0,
        }}

        print("NO_SIDE_EFFECTS")
        """
    )

    assert_probe_passed(result)
