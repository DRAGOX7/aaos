"""Application-owned, bounded readers for sensitive Moodle auth state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from aaos.moodle.models import AuthPaths

MAX_API_TOKEN_BYTES = 8192
MAX_BROWSER_STATE_BYTES = 4 * 1024 * 1024


class CredentialReadError(RuntimeError):
    """Raised when the configured API token cannot be safely read."""


class BrowserStateError(RuntimeError):
    """Raised when dedicated browser state is unavailable or unbound."""


class MoodleCredentialReader(Protocol):
    def read_api_token(self) -> str:
        """Read the selected source's token during an explicit collection."""


class FileMoodleCredentialReader:
    def __init__(self, paths: AuthPaths) -> None:
        self._paths = paths

    def read_api_token(self) -> str:
        return read_api_token(self._paths)


class MoodleBrowserStateLoader:
    def __init__(self, paths: AuthPaths, expected_origin: str) -> None:
        self._paths = paths
        self._expected_origin = expected_origin.rstrip("/")

    def load_state_path(self) -> Path:
        return load_browser_state_path(self._paths, self._expected_origin)


def read_api_token(paths: AuthPaths) -> str:
    """Read only the exact application-owned token file with bounded decoding."""
    try:
        token_path = paths.api_token_path
        if token_path.resolve(strict=False) != token_path:
            raise CredentialReadError("API credential unavailable")
        with token_path.open("rb") as handle:
            payload = handle.read(MAX_API_TOKEN_BYTES + 1)
    except (OSError, ValueError):
        raise CredentialReadError("API credential unavailable") from None
    if len(payload) > MAX_API_TOKEN_BYTES:
        raise CredentialReadError("API credential unavailable")
    try:
        token = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise CredentialReadError("API credential unavailable") from None
    if not token or any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise CredentialReadError("API credential unavailable")
    return token


def load_browser_state_path(paths: AuthPaths, expected_origin: str) -> Path:
    """Validate a dedicated storage-state artifact without exposing its contents."""
    try:
        state_path = paths.browser_state_dir / "storage_state.json"
        if state_path.resolve(strict=False).parent != paths.browser_state_dir.resolve(strict=False):
            raise BrowserStateError("browser state unavailable")
        payload = state_path.read_bytes()
        if len(payload) > MAX_BROWSER_STATE_BYTES:
            raise BrowserStateError("browser state unavailable")
        document = json.loads(payload.decode("utf-8"))
        origins = document.get("origins")
        if not isinstance(origins, list) or expected_origin.rstrip("/") not in {
            item.get("origin", "").rstrip("/")
            for item in origins
            if isinstance(item, dict) and isinstance(item.get("origin"), str)
        }:
            raise BrowserStateError("browser state unavailable")
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
        raise BrowserStateError("browser state unavailable") from None
    return state_path
