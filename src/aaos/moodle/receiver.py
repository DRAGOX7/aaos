"""Offline-safe Windows activation receiver for one Moodle callback URI."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Sequence
from urllib.parse import urlsplit

from aaos.moodle.auth import CredentialWriteError, write_api_token
from aaos.moodle.bootstrap import (
    BootstrapError,
    MoodleBootstrapConfig,
    _extract_web_service_token,
)
from aaos.moodle.models import AuthPaths, MoodleSettings, resolve_auth_paths

PENDING_ATTEMPT_FILENAME = "pending_bootstrap.json"
PENDING_ATTEMPT_MAX_BYTES = 4096
PENDING_ATTEMPT_TTL_SECONDS = 300
_PASSPORT_MAX_BYTES = 512
_ATTEMPT_ID_MAX_BYTES = 128
_LOCK_FILENAME = ".pending_bootstrap.lock"

__all__ = [
    "PENDING_ATTEMPT_FILENAME",
    "PENDING_ATTEMPT_TTL_SECONDS",
    "PendingBootstrapAttempt",
    "ReceiverError",
    "begin_bootstrap_attempt",
    "cancel_bootstrap_attempt",
    "main",
    "receive_callback",
]


class ReceiverError(RuntimeError):
    """Raised for any fixed, redacted callback-receiver failure."""


def _failure() -> ReceiverError:
    return ReceiverError("Moodle callback receiver failed")


@dataclass(frozen=True, slots=True)
class PendingBootstrapAttempt:
    """Non-secret metadata for one pending callback attempt."""

    source_system: str
    canonical_wwwroot: str
    passport: str = field(repr=False)
    attempt_id: str = field(repr=False)
    created_at: int
    expires_at: int


def _pending_path(paths: AuthPaths) -> Path:
    root = paths.browser_state_dir
    path = root / PENDING_ATTEMPT_FILENAME
    try:
        resolved_root = root.resolve(strict=False)
        if (
            resolved_root != root
            or path.resolve(strict=False) != path
            or path.resolve(strict=False).parent != resolved_root
        ):
            raise _failure()
    except (OSError, RuntimeError, ValueError):
        raise _failure() from None
    return path


def _lock_path(paths: AuthPaths) -> Path:
    root = paths.browser_state_dir
    path = root / _LOCK_FILENAME
    try:
        if path.resolve(strict=False) != path or path.resolve(strict=False).parent != root:
            raise _failure()
    except (OSError, RuntimeError, ValueError):
        raise _failure() from None
    return path


def _acquire_lock(paths: AuthPaths) -> Path:
    lock = _lock_path(paths)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise _failure() from None
    for _ in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime <= PENDING_ATTEMPT_TTL_SECONDS:
                    raise _failure()
                lock.unlink()
            except ReceiverError:
                raise
            except OSError:
                raise _failure() from None
        except OSError:
            raise _failure() from None
    raise _failure()


def _release_lock(lock: Path) -> None:
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        raise _failure() from None


def _safe_string(value: object, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise _failure()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _failure() from None
    if len(encoded) > maximum_bytes or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _failure()
    return value


def _current_time(now: int | None) -> int:
    if now is None:
        return int(time.time())
    if isinstance(now, bool) or not isinstance(now, int):
        raise _failure()
    return now


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("invalid JSON number")


def _read_attempt(
    path: Path,
    config: MoodleBootstrapConfig,
    now: int,
    *,
    allow_expired: bool = False,
) -> PendingBootstrapAttempt:
    try:
        with path.open("rb") as handle:
            payload = handle.read(PENDING_ATTEMPT_MAX_BYTES + 1)
    except (OSError, ValueError):
        raise _failure() from None
    if len(payload) > PENDING_ATTEMPT_MAX_BYTES:
        raise _failure()
    try:
        document = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, TypeError, ValueError):
        raise _failure() from None
    if not isinstance(document, dict) or set(document) != {
        "source_system",
        "canonical_wwwroot",
        "passport",
        "attempt_id",
        "created_at",
        "expires_at",
    }:
        raise _failure()
    source_system = _safe_string(document["source_system"], 256)
    canonical_wwwroot = _safe_string(document["canonical_wwwroot"], 2048)
    passport = _safe_string(document["passport"], _PASSPORT_MAX_BYTES)
    attempt_id = _safe_string(document["attempt_id"], _ATTEMPT_ID_MAX_BYTES)
    created_at = document["created_at"]
    expires_at = document["expires_at"]
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= created_at
        or expires_at - created_at > PENDING_ATTEMPT_TTL_SECONDS
        or len(passport.encode("utf-8")) < 32
        or len(attempt_id.encode("utf-8")) < 16
        or created_at > now + 5
        or source_system != config.source_system
        or canonical_wwwroot != config.canonical_wwwroot
    ):
        raise _failure()
    if not allow_expired and now > expires_at:
        try:
            path.unlink()
        except OSError:
            raise _failure() from None
        raise _failure()
    return PendingBootstrapAttempt(
        source_system=source_system,
        canonical_wwwroot=canonical_wwwroot,
        passport=passport,
        attempt_id=attempt_id,
        created_at=created_at,
        expires_at=expires_at,
    )


def begin_bootstrap_attempt(
    config: MoodleBootstrapConfig, paths: AuthPaths, *, now: int | None = None
) -> PendingBootstrapAttempt:
    """Create one bounded pending attempt for a future browser activation."""
    current = _current_time(now)
    path = _pending_path(paths)
    lock = _acquire_lock(paths)
    try:
        if path.exists():
            existing = _read_attempt(path, config, current, allow_expired=True)
            if existing.expires_at > current:
                raise _failure()
            try:
                path.unlink()
            except OSError:
                raise _failure() from None

        attempt = PendingBootstrapAttempt(
            source_system=config.source_system,
            canonical_wwwroot=config.canonical_wwwroot,
            passport=secrets.token_urlsafe(32),
            attempt_id=secrets.token_urlsafe(16),
            created_at=current,
            expires_at=current + PENDING_ATTEMPT_TTL_SECONDS,
        )
        document = {
            "source_system": attempt.source_system,
            "canonical_wwwroot": attempt.canonical_wwwroot,
            "passport": attempt.passport,
            "attempt_id": attempt.attempt_id,
            "created_at": attempt.created_at,
            "expires_at": attempt.expires_at,
        }
        temporary = path.with_name(f".{PENDING_ATTEMPT_FILENAME}.{attempt.attempt_id}.tmp")
        if temporary.resolve(strict=False) != temporary:
            raise _failure()
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
        os.replace(temporary, path)
    except (OSError, UnicodeError, TypeError, ValueError):
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            raise _failure() from None
        raise _failure() from None
    finally:
        _release_lock(lock)
    return attempt


def _read_attempt_id(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            payload = handle.read(PENDING_ATTEMPT_MAX_BYTES + 1)
    except (OSError, ValueError):
        raise _failure() from None
    if len(payload) > PENDING_ATTEMPT_MAX_BYTES:
        raise _failure()
    try:
        document = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, TypeError, ValueError):
        raise _failure() from None
    if not isinstance(document, dict):
        raise _failure()
    attempt_id = _safe_string(document.get("attempt_id"), _ATTEMPT_ID_MAX_BYTES)
    if len(attempt_id.encode("utf-8")) < 16:
        raise _failure()
    return attempt_id


def cancel_bootstrap_attempt(paths: AuthPaths, attempt_id: str) -> None:
    """Invalidate the identified pending attempt without deleting a newer one."""
    if len(_safe_string(attempt_id, _ATTEMPT_ID_MAX_BYTES).encode("utf-8")) < 16:
        raise _failure()
    path = _pending_path(paths)
    lock = _acquire_lock(paths)
    try:
        if not path.exists() or _read_attempt_id(path) != attempt_id:
            return
        path.unlink()
    except OSError:
        raise _failure() from None
    finally:
        _release_lock(lock)


def receive_callback(
    config: MoodleBootstrapConfig,
    paths: AuthPaths,
    callback_uri: str,
    *,
    now: int | None = None,
) -> None:
    """Validate and consume exactly one callback without making network calls."""
    current = _current_time(now)
    path = _pending_path(paths)
    lock = _acquire_lock(paths)
    try:
        attempt = _read_attempt(path, config, current, allow_expired=False)
        try:
            web_service_token = _extract_web_service_token(config, attempt.passport, callback_uri)
        except (BootstrapError, TypeError, ValueError):
            try:
                path.unlink()
            except OSError:
                raise _failure() from None
            raise _failure() from None

        # Invalidation happens before credential persistence so a writer failure
        # cannot leave a replayable successful callback behind.
        try:
            path.unlink()
        except OSError:
            raise _failure() from None
        try:
            write_api_token(paths, web_service_token)
        except CredentialWriteError:
            raise _failure() from None
    finally:
        _release_lock(lock)


def _runtime_configuration() -> tuple[MoodleBootstrapConfig, AuthPaths]:
    source_system = os.environ.get("AAOS_MOODLE_SOURCE_SYSTEM")
    base_origin = os.environ.get("AAOS_MOODLE_BASE_ORIGIN")
    auth_alias = os.environ.get("AAOS_MOODLE_AUTH_ALIAS")
    data_root = os.environ.get("AAOS_DATA_ROOT")
    if source_system is None or base_origin is None or auth_alias is None or data_root is None:
        raise _failure()
    config = MoodleBootstrapConfig(
        source_system=source_system,
        canonical_wwwroot=base_origin,
        callback_kind="registered_uri",
        callback_scheme="aaos",
        timeout_seconds=PENDING_ATTEMPT_TTL_SECONDS,
    )
    parsed = urlsplit(config.canonical_wwwroot)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    settings = MoodleSettings(
        source_system=source_system,
        base_origin=origin,
        mode="api",
        auth_alias=auth_alias,
    )
    return config, resolve_auth_paths(settings, data_root=data_root)


def main(argv: Sequence[str] | None = None) -> int:
    """Handle one Windows protocol activation with fixed silent exit codes."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or not isinstance(arguments[0], str):
        return 2
    try:
        config, paths = _runtime_configuration()
        receive_callback(config, paths, arguments[0])
    except (ReceiverError, OSError, TypeError, ValueError):
        return 2
    return 0
