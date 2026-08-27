"""Security and lifecycle tests for the Phase 3 ingestion lease."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aaos.documents.lock import IngestionLease, IngestionLock, IngestionLockUnavailable


def test_first_acquisition_writes_only_a_random_nonce(tmp_path: Path) -> None:
    lock_path = tmp_path / "temporary" / "phase3-ingestion.lock"

    lease = IngestionLock(lock_path).acquire()

    nonce = lock_path.read_bytes()
    assert isinstance(lease, IngestionLease)
    assert len(nonce) == 32
    assert all(byte in b"0123456789abcdef" for byte in nonce)
    assert str(lock_path).encode() not in nonce
    lease.release()


def test_second_acquisition_fails_without_deleting_first_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"
    first = IngestionLock(lock_path).acquire()

    with pytest.raises(IngestionLockUnavailable) as caught:
        IngestionLock(lock_path).acquire()

    assert str(caught.value) == "document ingestion already active"
    assert lock_path.exists()
    first.release()


def test_context_exit_removes_owned_lock_on_normal_path(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"

    with IngestionLock(lock_path).acquire():
        assert lock_path.exists()

    assert not lock_path.exists()


def test_context_exit_removes_owned_lock_and_preserves_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"

    with pytest.raises(ValueError, match="application failure"):
        with IngestionLock(lock_path).acquire():
            raise ValueError("application failure")

    assert not lock_path.exists()


def test_release_is_idempotent_after_owned_removal(tmp_path: Path) -> None:
    lease = IngestionLock(tmp_path / "phase3.lock").acquire()

    lease.release()
    lease.release()


def test_nonce_mismatch_never_deletes_replacement_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"
    lease = IngestionLock(lock_path).acquire()
    lock_path.write_bytes(b"f" * 32)

    with pytest.raises(IngestionLockUnavailable):
        lease.release()

    assert lock_path.read_bytes() == b"f" * 32


def test_preexisting_lock_is_never_taken_over_or_deleted(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"
    lock_path.write_bytes(b"a" * 32)

    with pytest.raises(IngestionLockUnavailable):
        IngestionLock(lock_path).acquire()

    assert lock_path.read_bytes() == b"a" * 32


def test_nonce_write_failure_removes_only_the_newly_created_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "phase3.lock"

    def fail_fsync(descriptor: int) -> None:
        os.ftruncate(descriptor, 0)
        raise OSError("fsync failed")

    monkeypatch.setattr("aaos.documents.lock.os.fsync", fail_fsync)

    with pytest.raises(IngestionLockUnavailable):
        IngestionLock(lock_path).acquire()

    assert not lock_path.exists()


@pytest.mark.parametrize("path_kind", ("relative", "non_normalized", "outside_parent_file"))
def test_unsafe_lock_paths_fail_closed(tmp_path: Path, path_kind: str) -> None:
    if path_kind == "relative":
        path = Path("phase3.lock")
    elif path_kind == "non_normalized":
        path = tmp_path / "child" / ".." / "phase3.lock"
    else:
        parent = tmp_path / "not-a-directory"
        parent.write_text("file", encoding="ascii")
        path = parent / "phase3.lock"

    with pytest.raises(IngestionLockUnavailable):
        IngestionLock(path).acquire()


def test_symlink_ancestor_fails_closed_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks/reparse points are unavailable")

    with pytest.raises(IngestionLockUnavailable):
        IngestionLock(link / "phase3.lock").acquire()

    assert not (outside / "phase3.lock").exists()


def test_replaced_symlink_lock_fails_closed_without_following_or_deleting_target(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "phase3.lock"
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"f" * 32)
    lease = IngestionLock(lock_path).acquire()
    lease.release()
    lease = IngestionLock(lock_path).acquire()
    lock_path.unlink()
    try:
        lock_path.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks/reparse points are unavailable")

    with pytest.raises(IngestionLockUnavailable):
        lease.release()

    assert outside.exists()


def test_contention_prevents_downstream_work(tmp_path: Path) -> None:
    lock_path = tmp_path / "phase3.lock"
    first = IngestionLock(lock_path).acquire()
    calls: list[str] = []

    with pytest.raises(IngestionLockUnavailable):
        with IngestionLock(lock_path).acquire():
            calls.append("must-not-run")

    assert calls == []
    first.release()


def test_normal_database_and_content_store_paths_are_not_created(tmp_path: Path) -> None:
    lock_path = tmp_path / "temporary" / "phase3.lock"

    with IngestionLock(lock_path).acquire():
        pass

    assert not (tmp_path / "database" / "aaos.db").exists()
    assert not (tmp_path / "documents").exists()
