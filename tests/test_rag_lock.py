"""Security, process-exclusion, and lifecycle tests for the Phase 4 writer lease."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol

import pytest

from aaos.rag.config import resolve_rag_runtime_paths
from aaos.rag.contracts import RagErrorCode, RagOperationalError
from aaos.rag.lock import RagIndexLease, RagIndexLock, RagIndexLockUnavailable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMAL_DATABASE = PROJECT_ROOT / "data/database/aaos.db"
NORMAL_RAG_ROOT = PROJECT_ROOT / "data/rag"
PROCESS_SIGNAL_TIMEOUT_SECONDS = 15.0


class _ProcessConnection(Protocol):
    def send(self, value: object) -> None: ...

    def recv(self) -> object: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def close(self) -> None: ...


class _TestProcess(Protocol):
    @property
    def exitcode(self) -> int | None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...


def _hold_lock_until_released(lock_path: str, connection: _ProcessConnection) -> None:
    with RagIndexLock(Path(lock_path)).acquire():
        connection.send("ACQUIRED")
        if connection.recv() != "RELEASE":
            raise RuntimeError("invalid test process signal")
    connection.send("RELEASED")
    connection.close()


def _acquire_then_exit_without_release(lock_path: str, connection: _ProcessConnection) -> None:
    _lease = RagIndexLock(Path(lock_path)).acquire()
    connection.send("ACQUIRED")
    connection.close()
    os._exit(0)


def _stop_test_process(process: _TestProcess, connection: _ProcessConnection) -> None:
    if process.is_alive():
        try:
            connection.send("RELEASE")
        except (BrokenPipeError, EOFError, OSError):
            pass
        process.join(timeout=PROCESS_SIGNAL_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=PROCESS_SIGNAL_TIMEOUT_SECONDS)
    connection.close()


def test_first_acquisition_uses_injected_rag_path_and_writes_only_nonce(tmp_path: Path) -> None:
    lock_path = resolve_rag_runtime_paths(tmp_path).index_lock_path

    lease = RagIndexLock(lock_path).acquire()

    nonce = lock_path.read_bytes()
    assert isinstance(lease, RagIndexLease)
    assert len(nonce) == 32
    assert all(byte in b"0123456789abcdef" for byte in nonce)
    assert str(lock_path).encode() not in nonce
    lease.release()


def test_contention_fails_immediately_with_safe_typed_error(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    first = RagIndexLock(lock_path).acquire()

    with pytest.raises(RagIndexLockUnavailable) as caught:
        RagIndexLock(lock_path).acquire()

    assert isinstance(caught.value, RagOperationalError)
    assert caught.value.code is RagErrorCode.RAG_INDEX_LOCKED
    assert str(caught.value) == "rag operation failed"
    assert str(lock_path) not in str(caught.value)
    assert lock_path.exists()
    first.release()


def test_same_lock_object_is_non_reentrant(tmp_path: Path) -> None:
    lock = RagIndexLock(tmp_path / "phase4-rag-index.lock")
    lease = lock.acquire()

    with pytest.raises(RagIndexLockUnavailable):
        lock.acquire()

    lease.release()


def test_release_is_owner_scoped_and_idempotent(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    lease = RagIndexLock(lock_path).acquire()

    lease.release()
    lease.release()

    with RagIndexLock(lock_path).acquire():
        assert lock_path.exists()
    assert not lock_path.exists()


def test_context_exit_releases_after_application_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"

    with pytest.raises(ValueError, match="application failure"):
        with RagIndexLock(lock_path).acquire():
            raise ValueError("application failure")

    assert not lock_path.exists()
    with RagIndexLock(lock_path).acquire():
        pass


def test_replaced_lock_is_never_removed_by_previous_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    lease = RagIndexLock(lock_path).acquire()
    original_nonce = lock_path.read_bytes()
    lock_path.unlink()
    lock_path.write_bytes(original_nonce)

    with pytest.raises(RagIndexLockUnavailable):
        lease.release()

    assert lock_path.read_bytes() == original_nonce


def test_preexisting_or_malformed_lock_is_never_taken_over(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    lock_path.write_bytes(b"malformed")

    with pytest.raises(RagIndexLockUnavailable):
        RagIndexLock(lock_path).acquire()

    assert lock_path.read_bytes() == b"malformed"


def test_failed_nonce_flush_removes_only_newly_created_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"

    def fail_fsync(descriptor: int) -> None:
        os.ftruncate(descriptor, 0)
        raise OSError("private filesystem detail")

    monkeypatch.setattr("aaos.rag.lock.os.fsync", fail_fsync)

    with pytest.raises(RagIndexLockUnavailable) as caught:
        RagIndexLock(lock_path).acquire()

    assert str(caught.value) == "rag operation failed"
    assert "private filesystem detail" not in str(caught.value)
    assert not lock_path.exists()


@pytest.mark.parametrize("path_kind", ("relative", "non_normalized", "parent_file"))
def test_unsafe_lock_paths_fail_closed(tmp_path: Path, path_kind: str) -> None:
    if path_kind == "relative":
        lock_path = Path("phase4-rag-index.lock")
    elif path_kind == "non_normalized":
        lock_path = tmp_path / "child" / ".." / "phase4-rag-index.lock"
    else:
        parent = tmp_path / "not-a-directory"
        parent.write_text("file", encoding="ascii")
        lock_path = parent / "phase4-rag-index.lock"

    with pytest.raises(RagIndexLockUnavailable):
        RagIndexLock(lock_path).acquire()


def test_symlink_or_reparse_ancestor_fails_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks/reparse points are unavailable")

    with pytest.raises(RagIndexLockUnavailable):
        RagIndexLock(link / "phase4-rag-index.lock").acquire()

    assert not (outside / "phase4-rag-index.lock").exists()


def test_contention_prevents_downstream_mutation(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    first = RagIndexLock(lock_path).acquire()
    mutations: list[str] = []

    with pytest.raises(RagIndexLockUnavailable):
        with RagIndexLock(lock_path).acquire():
            mutations.append("must-not-run")

    assert mutations == []
    first.release()


def test_cross_process_writer_is_excluded_until_owner_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe()
    process = context.Process(
        target=_hold_lock_until_released,
        args=(str(lock_path), child_connection),
    )
    process.start()
    child_connection.close()
    try:
        assert parent_connection.poll(PROCESS_SIGNAL_TIMEOUT_SECONDS)
        assert parent_connection.recv() == "ACQUIRED"
        with pytest.raises(RagIndexLockUnavailable):
            RagIndexLock(lock_path).acquire()

        parent_connection.send("RELEASE")
        assert parent_connection.poll(PROCESS_SIGNAL_TIMEOUT_SECONDS)
        assert parent_connection.recv() == "RELEASED"
        process.join(timeout=PROCESS_SIGNAL_TIMEOUT_SECONDS)
        assert process.exitcode == 0

        with RagIndexLock(lock_path).acquire():
            pass
    finally:
        _stop_test_process(process, parent_connection)


def test_process_exit_leaves_stale_marker_fail_closed_without_takeover(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe()
    process = context.Process(
        target=_acquire_then_exit_without_release,
        args=(str(lock_path), child_connection),
    )
    process.start()
    child_connection.close()
    try:
        assert parent_connection.poll(PROCESS_SIGNAL_TIMEOUT_SECONDS)
        assert parent_connection.recv() == "ACQUIRED"
        process.join(timeout=PROCESS_SIGNAL_TIMEOUT_SECONDS)
        assert process.exitcode == 0
        assert lock_path.exists()

        with pytest.raises(RagIndexLockUnavailable):
            RagIndexLock(lock_path).acquire()
    finally:
        _stop_test_process(process, parent_connection)
        lock_path.unlink(missing_ok=True)


def test_import_is_offline_and_creates_no_normal_or_heavy_runtime_state(tmp_path: Path) -> None:
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_RAG_ROOT.exists()
    script = """
import pathlib
import socket
import sys

import httpx

def forbidden(*_args, **_kwargs):
    raise AssertionError("network access during lock import")

socket.socket = forbidden
socket.create_connection = forbidden

import aaos.rag.lock

assert "FlagEmbedding" not in sys.modules
assert "chromadb" not in sys.modules
assert "ragas" not in sys.modules
assert "torch" not in sys.modules
assert not pathlib.Path(sys.argv[1]).exists()
assert not pathlib.Path(sys.argv[2]).exists()
print("RAG_LOCK_IMPORT_SAFE")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(NORMAL_DATABASE), str(NORMAL_RAG_ROOT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "RAG_LOCK_IMPORT_SAFE\n"
    assert completed.stderr == ""
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_RAG_ROOT.exists()


def test_two_distinct_injected_lock_paths_do_not_block_each_other(tmp_path: Path) -> None:
    first_path = tmp_path / "first" / "phase4-rag-index.lock"
    second_path = tmp_path / "second" / "phase4-rag-index.lock"

    with RagIndexLock(first_path).acquire(), RagIndexLock(second_path).acquire():
        assert first_path.exists()
        assert second_path.exists()


def test_assert_owned_accepts_current_owner(tmp_path: Path) -> None:
    lock = RagIndexLock(tmp_path / "phase4-rag-index.lock")
    lease = lock.acquire()
    try:
        lock.assert_owned(lease)
    finally:
        lease.release()


def test_assert_owned_rejects_released_lease(tmp_path: Path) -> None:
    lock = RagIndexLock(tmp_path / "phase4-rag-index.lock")
    lease = lock.acquire()
    lease.release()

    with pytest.raises(RagIndexLockUnavailable):
        lock.assert_owned(lease)


def test_assert_owned_rejects_foreign_lock_lease(tmp_path: Path) -> None:
    first = RagIndexLock(tmp_path / "first.lock")
    second = RagIndexLock(tmp_path / "second.lock")
    lease = first.acquire()
    try:
        with pytest.raises(RagIndexLockUnavailable):
            second.assert_owned(lease)
    finally:
        lease.release()


def test_assert_owned_rejects_replaced_marker(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase4-rag-index.lock"
    lock = RagIndexLock(lock_path)
    lease = lock.acquire()
    original_nonce = lock_path.read_bytes()
    lock_path.unlink()
    lock_path.write_bytes(original_nonce)

    with pytest.raises(RagIndexLockUnavailable):
        lock.assert_owned(lease)
