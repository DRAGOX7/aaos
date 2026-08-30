"""Independent single-writer lease for Phase 4 RAG index mutation."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from aaos.rag.contracts import RagErrorCode, RagOperationalError

_NONCE_LENGTH = 32
_LOCK_MODE = os.O_CREAT | os.O_EXCL | os.O_WRONLY


class RagIndexLockUnavailable(RagOperationalError):
    """Raised when exclusive RAG writer ownership cannot be proven safely."""

    def __init__(self) -> None:
        super().__init__(RagErrorCode.RAG_INDEX_LOCKED)


class RagIndexLease:
    """An ownership-scoped lease that can remove only its own writer lock."""

    def __init__(self, lock_path: Path, nonce: bytes, identity: tuple[int, int]) -> None:
        self._lock_path = lock_path
        self._nonce = nonce
        self._identity = identity
        self._released = False

    def __enter__(self) -> RagIndexLease:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()

    def release(self) -> None:
        """Release only the lock file whose nonce and identity this lease owns."""
        if self._released:
            return
        try:
            nonce, identity = _read_lock_state(self._lock_path)
        except FileNotFoundError:
            self._released = True
            return
        except (OSError, RuntimeError, ValueError):
            raise RagIndexLockUnavailable() from None
        if nonce != self._nonce or identity != self._identity:
            raise RagIndexLockUnavailable()
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            self._released = True
            return
        except OSError:
            raise RagIndexLockUnavailable() from None
        self._released = True


class RagIndexLock:
    """Atomically acquire one application-owned Phase 4 writer lock."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def acquire(self) -> RagIndexLease:
        """Acquire immediately or fail closed without waiting or taking over."""
        try:
            _validate_lock_path(self._path)
        except RagIndexLockUnavailable:
            raise
        except (OSError, RuntimeError, ValueError):
            raise RagIndexLockUnavailable() from None
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        nonce = secrets.token_hex(_NONCE_LENGTH // 2).encode("ascii")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _ensure_directory(self._path.parent)
            _reject_reparse_ancestors(self._path.parent)
            descriptor = os.open(self._path, _LOCK_MODE)
            identity = _file_identity(descriptor)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(nonce)
                handle.flush()
                os.fsync(handle.fileno())
            if identity is None:
                raise RagIndexLockUnavailable()
            return RagIndexLease(self._path, nonce, identity)
        except FileExistsError:
            raise RagIndexLockUnavailable() from None
        except (OSError, RuntimeError, ValueError):
            _remove_created_lock(self._path, identity)
            raise RagIndexLockUnavailable() from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def assert_owned(self, lease: RagIndexLease) -> None:
        """Require a live lease for this exact marker without acquiring it again."""
        if (
            not isinstance(lease, RagIndexLease)
            or lease._released
            or lease._lock_path != self._path
        ):
            raise RagIndexLockUnavailable()
        try:
            nonce, identity = _read_lock_state(self._path)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            raise RagIndexLockUnavailable() from None
        if nonce != lease._nonce or identity != lease._identity:
            raise RagIndexLockUnavailable()


def _validate_lock_path(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=False) != path or not path.name:
        raise RagIndexLockUnavailable()
    _reject_reparse_ancestors(path.parent)


def _read_lock_state(path: Path) -> tuple[bytes, tuple[int, int]]:
    metadata = path.lstat()
    if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise RagIndexLockUnavailable()
    nonce = path.read_bytes()
    if len(nonce) != _NONCE_LENGTH or any(byte not in b"0123456789abcdef" for byte in nonce):
        raise RagIndexLockUnavailable()
    return nonce, _file_identity_from_stat(metadata)


def _remove_created_lock(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            not _is_reparse(metadata)
            and stat.S_ISREG(metadata.st_mode)
            and _file_identity_from_stat(metadata) == identity
        ):
            path.unlink()
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        pass


def _ensure_directory(path: Path) -> None:
    metadata = path.lstat()
    if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise RagIndexLockUnavailable()


def _reject_reparse_ancestors(path: Path) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise RagIndexLockUnavailable() from None
        else:
            if _is_reparse(metadata):
                raise RagIndexLockUnavailable()
        if current.parent == current:
            return
        current = current.parent


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(
        stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _file_identity(descriptor: int) -> tuple[int, int]:
    return _file_identity_from_stat(os.fstat(descriptor))


def _file_identity_from_stat(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


__all__ = ["RagIndexLease", "RagIndexLock", "RagIndexLockUnavailable"]
