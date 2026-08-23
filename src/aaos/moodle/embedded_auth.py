"""Narrow, offline-testable contract for a human-controlled WebView2 login."""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlencode, urlsplit

EmbeddedAuthResult = Literal["CALLBACK_DISPATCHED", "USER_CANCELLED", "TIMED_OUT", "HOST_ERROR"]
EmbeddedAuthFailureStage = Literal[
    "WEBVIEW2_HOST_ERROR",
    "WEBVIEW2_TIMEOUT",
    "WEBVIEW2_CLEANUP",
]
_LAUNCH_PATH = "/current/admin/tool/mobile/launch.php"
_MOBILE_SERVICE = "moodle_mobile_app"
_CALLBACK_SCHEME = "aaos"
_MIN_PASSPORT_BYTES = 32


class WebView2RuntimeUnavailable(RuntimeError):
    """Raised when the native WebView2 runtime adapter is not available."""

    def __init__(self) -> None:
        super().__init__("WebView2 embedded authentication is unavailable")


class WebView2Runtime(Protocol):
    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        """Run one headed, isolated WebView2 session without exposing browser data."""


def build_embedded_launch_url(launch_url: str, passport: str) -> str:
    """Build the fixed Moodle mobile launch URL from the returned launch URL."""
    if (
        not isinstance(launch_url, str)
        or not isinstance(passport, str)
        or len(passport.encode("utf-8")) < _MIN_PASSPORT_BYTES
    ):
        raise ValueError("embedded authentication launch is invalid")
    try:
        parsed = urlsplit(launch_url.rstrip("/"))
        _ = parsed.port
    except (TypeError, ValueError):
        raise ValueError("embedded authentication launch is invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname != "elearn.just.edu.jo"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != _LAUNCH_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("embedded authentication launch is invalid")
    return (
        launch_url.rstrip("/")
        + "?"
        + urlencode(
            {"service": _MOBILE_SERVICE, "passport": passport, "urlscheme": _CALLBACK_SCHEME}
        )
    )


def is_allowed_external_scheme(scheme: str) -> bool:
    """Allow WebView2 OS dispatch only for the registered AAOS scheme."""
    return scheme == _CALLBACK_SCHEME


def is_allowed_new_window(url: str) -> bool:
    """Keep ordinary same-origin HTTPS auth navigation inside the managed host."""
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "elearn.just.edu.jo"
        and parsed.port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


@dataclass(slots=True)
class EphemeralProfile:
    """One unique attempt-scoped WebView2 user-data directory."""

    path: Path

    @classmethod
    def create(cls, root: Path) -> EphemeralProfile:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"embedded-{secrets.token_hex(16)}"
        path.mkdir()
        return cls(path)

    def cleanup(self) -> None:
        last_error: OSError | None = None
        for _ in range(3):
            try:
                shutil.rmtree(self.path)
                return
            except FileNotFoundError:
                return
            except OSError as error:
                last_error = error
        raise WebView2RuntimeUnavailable() from last_error


class WebView2EmbeddedAuthHost:
    """Application-owned host seam with no DOM, token, or callback-URI surface."""

    def __init__(self, runtime: WebView2Runtime | None) -> None:
        self._runtime = runtime
        self.last_interaction_result: EmbeddedAuthResult | None = None
        self.last_cleanup_pass: bool | None = None
        self.last_failure_stage: EmbeddedAuthFailureStage | None = None

    @classmethod
    def unavailable(cls) -> WebView2EmbeddedAuthHost:
        return cls(None)

    def start(
        self, launch_url: str, profile_root: Path, passport: str, timeout_seconds: int
    ) -> EmbeddedAuthResult:
        self.last_interaction_result = None
        self.last_cleanup_pass = None
        self.last_failure_stage = None
        if self._runtime is None:
            raise WebView2RuntimeUnavailable()
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise ValueError("embedded authentication launch is invalid")
        profile = EphemeralProfile.create(profile_root)
        try:
            fixed_url = build_embedded_launch_url(launch_url, passport)
            try:
                result = self._runtime.run(fixed_url, profile.path, timeout_seconds)
            except Exception:
                self.last_failure_stage = "WEBVIEW2_HOST_ERROR"
                return "HOST_ERROR"
            if result in {"CALLBACK_DISPATCHED", "USER_CANCELLED", "TIMED_OUT", "HOST_ERROR"}:
                self.last_interaction_result = result
            if not getattr(self._runtime, "cleanup_pass", True):
                self.last_cleanup_pass = False
                self.last_failure_stage = "WEBVIEW2_CLEANUP"
                return "HOST_ERROR"
            if result not in {"CALLBACK_DISPATCHED", "USER_CANCELLED", "TIMED_OUT", "HOST_ERROR"}:
                self.last_failure_stage = "WEBVIEW2_HOST_ERROR"
                return "HOST_ERROR"
            if result == "TIMED_OUT":
                self.last_failure_stage = "WEBVIEW2_TIMEOUT"
            elif result == "HOST_ERROR":
                self.last_failure_stage = "WEBVIEW2_HOST_ERROR"
            return result
        finally:
            try:
                profile.cleanup()
                if self.last_cleanup_pass is None:
                    self.last_cleanup_pass = True
            except WebView2RuntimeUnavailable:
                self.last_cleanup_pass = False
                self.last_failure_stage = "WEBVIEW2_CLEANUP"
                return "HOST_ERROR"
