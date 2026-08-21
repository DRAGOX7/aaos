"""Offline-safe Windows activation receiver for one Moodle callback URI."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NoReturn, Sequence
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
COMPLETION_FILENAME = "bootstrap_completion.json"
_PASSPORT_MAX_BYTES = 512
_ATTEMPT_ID_MAX_BYTES = 128
_LOCK_FILENAME = ".pending_bootstrap.lock"
_REQUIRED_CONFIG_NAMES = (
    "AAOS_MOODLE_SOURCE_SYSTEM",
    "AAOS_MOODLE_BASE_ORIGIN",
    "AAOS_MOODLE_AUTH_ALIAS",
    "AAOS_DATA_ROOT",
)
_MISSING = object()

__all__ = [
    "COMPLETION_FILENAME",
    "PENDING_ATTEMPT_FILENAME",
    "PENDING_ATTEMPT_TTL_SECONDS",
    "PendingBootstrapAttempt",
    "BootstrapCompletion",
    "ReceiverError",
    "begin_bootstrap_attempt",
    "cancel_bootstrap_attempt",
    "main",
    "receive_callback",
    "wait_for_bootstrap_completion",
]


class ReceiverError(RuntimeError):
    """Raised for any fixed, redacted callback-receiver failure."""


def _failure() -> ReceiverError:
    return ReceiverError("Moodle callback receiver failed")


def _configuration_unavailable() -> ReceiverError:
    return ReceiverError("Moodle callback configuration unavailable")


def _configuration_invalid() -> ReceiverError:
    return ReceiverError("Moodle callback configuration invalid")


def _configuration_conflict() -> ReceiverError:
    return ReceiverError("Moodle callback configuration conflict")


@dataclass(frozen=True, slots=True)
class PendingBootstrapAttempt:
    """Non-secret metadata for one pending callback attempt."""

    source_system: str
    canonical_wwwroot: str
    passport: str = field(repr=False)
    attempt_id: str = field(repr=False)
    created_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class BootstrapCompletion:
    """Non-secret proof that one exact callback persisted its token."""

    source_system: str
    canonical_wwwroot: str
    attempt_id: str
    completed_at: int


def _read_user_environment() -> dict[str, object]:
    """Read only the approved per-user configuration names on Windows."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        raise _configuration_unavailable() from None
    values: dict[str, object] = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ)
    except FileNotFoundError:
        return values
    except OSError:
        raise _configuration_unavailable() from None
    try:
        with key:
            for name in _REQUIRED_CONFIG_NAMES:
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    continue
                except OSError:
                    raise _configuration_unavailable() from None
    except AttributeError:
        # Keep the production boundary safe if a platform shim is incomplete.
        raise _configuration_unavailable() from None
    return values


def _validated_configuration_values() -> dict[str, str]:
    user_values = _read_user_environment() if sys.platform == "win32" else {}
    values: dict[str, str] = {}
    for name in _REQUIRED_CONFIG_NAMES:
        process_value: object = os.environ.get(name, _MISSING)
        user_value = user_values.get(name, _MISSING)
        if process_value is not _MISSING and user_value is not _MISSING:
            if process_value != user_value:
                raise _configuration_conflict()
        selected = process_value if process_value is not _MISSING else user_value
        if selected is _MISSING:
            raise _configuration_unavailable()
        if (
            not isinstance(selected, str)
            or not selected.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in selected)
        ):
            raise _configuration_invalid()
        if name == "AAOS_DATA_ROOT":
            try:
                if not Path(selected).is_absolute():
                    raise _configuration_invalid()
            except (OSError, RuntimeError, ValueError):
                raise _configuration_invalid() from None
        values[name] = selected
    return values


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


def _completion_path(paths: AuthPaths) -> Path:
    root = paths.browser_state_dir
    path = root / COMPLETION_FILENAME
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


def _write_completion(
    paths: AuthPaths, attempt: PendingBootstrapAttempt, completed_at: int
) -> None:
    path = _completion_path(paths)
    document = {
        "source_system": attempt.source_system,
        "canonical_wwwroot": attempt.canonical_wwwroot,
        "attempt_id": attempt.attempt_id,
        "completed_at": completed_at,
    }
    temporary = path.with_name(f".{COMPLETION_FILENAME}.{attempt.attempt_id}.tmp")
    try:
        if temporary.resolve(strict=False) != temporary:
            raise _failure()
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
        os.replace(temporary, path)
    except (OSError, UnicodeError, TypeError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            raise _failure() from None
        raise _failure() from None


def _read_completion(
    paths: AuthPaths, attempt: PendingBootstrapAttempt, now: int
) -> BootstrapCompletion:
    path = _completion_path(paths)
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
        "attempt_id",
        "completed_at",
    }:
        raise _failure()
    source_system = _safe_string(document["source_system"], 256)
    canonical_wwwroot = _safe_string(document["canonical_wwwroot"], 2048)
    attempt_id = _safe_string(document["attempt_id"], _ATTEMPT_ID_MAX_BYTES)
    completed_at = document["completed_at"]
    if (
        isinstance(completed_at, bool)
        or not isinstance(completed_at, int)
        or source_system != attempt.source_system
        or canonical_wwwroot != attempt.canonical_wwwroot
        or attempt_id != attempt.attempt_id
        or completed_at < attempt.created_at
        or completed_at > attempt.expires_at
        or now > attempt.expires_at
    ):
        raise _failure()
    return BootstrapCompletion(source_system, canonical_wwwroot, attempt_id, completed_at)


def wait_for_bootstrap_completion(
    paths: AuthPaths,
    attempt: PendingBootstrapAttempt,
    *,
    timeout_seconds: int,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> BootstrapCompletion:
    """Consume bounded, non-secret completion proof for exactly one attempt."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise _failure()
    try:
        deadline = monotonic() + timeout_seconds
    except (OSError, TypeError, ValueError):
        raise _failure() from None
    path = _completion_path(paths)
    while True:
        try:
            current = int(clock())
            remaining = deadline - monotonic()
        except (OSError, TypeError, ValueError):
            raise _failure() from None
        if current > attempt.expires_at or remaining <= 0:
            raise _failure()
        if path.exists():
            completion = _read_completion(paths, attempt, current)
            try:
                path.unlink()
            except OSError:
                raise _failure() from None
            return completion
        try:
            sleeper(min(0.05, remaining))
        except (OSError, TypeError, ValueError):
            raise _failure() from None


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

        try:
            _completion_path(paths).unlink(missing_ok=True)
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
        _write_completion(paths, attempt, current)
    finally:
        _release_lock(lock)


def load_runtime_moodle_configuration() -> tuple[MoodleBootstrapConfig, MoodleSettings, AuthPaths]:
    """Load the validated non-secret runtime configuration for one Moodle command."""
    values = _validated_configuration_values()
    source_system = values["AAOS_MOODLE_SOURCE_SYSTEM"]
    base_origin = values["AAOS_MOODLE_BASE_ORIGIN"]
    auth_alias = values["AAOS_MOODLE_AUTH_ALIAS"]
    data_root = values["AAOS_DATA_ROOT"]
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
    return config, settings, resolve_auth_paths(settings, data_root=data_root)


def _runtime_configuration() -> tuple[MoodleBootstrapConfig, AuthPaths]:
    config, _settings, paths = load_runtime_moodle_configuration()
    return config, paths


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
