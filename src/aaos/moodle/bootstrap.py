"""Offline-testable Moodle mobile registered-URI authentication bootstrap."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import unquote_to_bytes, urlencode, urlsplit

from aaos.moodle.auth import CredentialWriteError, write_api_token
from aaos.moodle.models import AuthPaths

# The native interaction timer starts after WebView2 environment initialization.
WEBVIEW2_INTERACTION_TIMEOUT_SECONDS = 600
WEBVIEW2_SETUP_ALLOWANCE_SECONDS = 60
# Dispatch through the registered OS URI handler is bounded separately from interaction.
CALLBACK_DELIVERY_GRACE_SECONDS = 60
# Must remain aligned with the native helper's BrowserProcessExited wait.
NATIVE_CLEANUP_ALLOWANCE_SECONDS = 5
HELPER_PROCESS_MARGIN_SECONDS = 5
ATTEMPT_EXPIRY_SAFETY_SECONDS = 10

MOBILE_LAUNCH_PATH = "/admin/tool/mobile/launch.php"
MOBILE_SERVICE = "moodle_mobile_app"
MAX_ENCODED_APP_TOKEN_BYTES = 8192
MAX_DECODED_APP_TOKEN_BYTES = 8192
PASSPORT_BYTES = 32
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class BootstrapError(RuntimeError):
    """Raised for any safe, fixed authentication-bootstrap failure."""


class SystemBrowserLauncher(Protocol):
    def open_fixed_auth_url(self, url: str) -> None:
        """Open one application-built Moodle mobile launch URL."""


class RegisteredUriReceiver(Protocol):
    def wait_for_callback(self, timeout_seconds: int) -> str:
        """Return one callback URI inside the private bootstrap boundary."""


@dataclass(frozen=True, slots=True)
class MoodleBootstrapConfig:
    source_system: str
    canonical_wwwroot: str
    callback_kind: Literal["registered_uri"]
    callback_scheme: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_system, str) or not self.source_system.strip():
            raise ValueError("Moodle bootstrap configuration is invalid")
        if not isinstance(self.canonical_wwwroot, str):
            raise ValueError("Moodle bootstrap configuration is invalid")
        try:
            parsed = urlsplit(self.canonical_wwwroot.rstrip("/"))
            _ = parsed.port
        except (TypeError, ValueError):
            raise ValueError("Moodle bootstrap configuration is invalid") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or self.callback_kind != "registered_uri"
            or self.callback_scheme != "aaos"
            or isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Moodle bootstrap configuration is invalid")
        object.__setattr__(self, "canonical_wwwroot", self.canonical_wwwroot.rstrip("/"))


def helper_process_timeout_seconds(interaction_timeout_seconds: int) -> int:
    """Return the bounded native-host lifetime for one human interaction."""
    if (
        isinstance(interaction_timeout_seconds, bool)
        or not isinstance(interaction_timeout_seconds, int)
        or interaction_timeout_seconds <= 0
    ):
        raise ValueError("Moodle authentication bootstrap is invalid")
    return (
        interaction_timeout_seconds
        + WEBVIEW2_SETUP_ALLOWANCE_SECONDS
        + NATIVE_CLEANUP_ALLOWANCE_SECONDS
        + HELPER_PROCESS_MARGIN_SECONDS
    )


def _failure() -> BootstrapError:
    return BootstrapError("Moodle authentication bootstrap failed")


def _decode_uri_component(value: str) -> bytes:
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError:
        raise _failure() from None
    if _INVALID_PERCENT_ESCAPE.search(value):
        raise _failure()
    return unquote_to_bytes(raw)


def _extract_web_service_token(
    config: MoodleBootstrapConfig, passport: str, callback_uri: str
) -> str:
    if not isinstance(callback_uri, str):
        raise _failure()
    try:
        parsed = urlsplit(callback_uri)
        parsed_port = parsed.port
    except (TypeError, ValueError):
        raise _failure() from None
    if (
        not callback_uri.startswith(f"{config.callback_scheme}://")
        or parsed.scheme != config.callback_scheme
        or parsed.netloc.count("token=") != 1
        or not parsed.netloc.startswith("token=")
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise _failure()

    encoded_payload = parsed.netloc[len("token=") :]
    if not encoded_payload:
        raise _failure()
    try:
        encoded_size = len(encoded_payload.encode("ascii"))
    except UnicodeEncodeError:
        raise _failure() from None
    if encoded_size > MAX_ENCODED_APP_TOKEN_BYTES:
        raise _failure()
    uri_decoded = _decode_uri_component(encoded_payload)
    if len(uri_decoded) > MAX_ENCODED_APP_TOKEN_BYTES:
        raise _failure()
    try:
        app_token = base64.b64decode(uri_decoded, validate=True)
    except (ValueError, binascii.Error):
        raise _failure() from None
    if len(app_token) > MAX_DECODED_APP_TOKEN_BYTES:
        raise _failure()
    try:
        envelope = app_token.decode("utf-8")
    except UnicodeDecodeError:
        raise _failure() from None
    components = envelope.split(":::")
    if len(components) not in {2, 3} or any(not component for component in components):
        raise _failure()
    expected_siteid = hashlib.md5((config.canonical_wwwroot + passport).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(components[0], expected_siteid):
        raise _failure()
    web_service_token = components[1]
    if (
        not web_service_token
        or len(web_service_token.encode("utf-8")) > MAX_DECODED_APP_TOKEN_BYTES
        or any(ord(char) < 32 or ord(char) == 127 for char in web_service_token)
    ):
        raise _failure()
    return web_service_token


async def bootstrap_token(
    config: MoodleBootstrapConfig,
    paths: AuthPaths,
    launcher: SystemBrowserLauncher,
    receiver: RegisteredUriReceiver,
) -> None:
    """Run one user-controlled registered-URI bootstrap without exposing secrets."""
    passport: str | None = None
    callback_uri: str | None = None
    web_service_token: str | None = None
    try:
        passport = secrets.token_urlsafe(PASSPORT_BYTES)
        launch_url = (
            config.canonical_wwwroot
            + MOBILE_LAUNCH_PATH
            + "?"
            + urlencode(
                {
                    "service": MOBILE_SERVICE,
                    "passport": passport,
                    "urlscheme": config.callback_scheme,
                }
            )
        )
        try:
            launcher.open_fixed_auth_url(launch_url)
            callback_uri = receiver.wait_for_callback(config.timeout_seconds)
            web_service_token = _extract_web_service_token(config, passport, callback_uri)
            write_api_token(paths, web_service_token)
        except BootstrapError:
            raise
        except CredentialWriteError:
            raise _failure() from None
        except Exception:
            raise _failure() from None
    finally:
        passport = None
        callback_uri = None
        web_service_token = None
