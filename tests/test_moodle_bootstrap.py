from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.moodle.auth import CredentialWriteError
from aaos.moodle.bootstrap import (
    MAX_DECODED_APP_TOKEN_BYTES,
    MAX_ENCODED_APP_TOKEN_BYTES,
    BootstrapError,
    MoodleBootstrapConfig,
    bootstrap_token,
)
from aaos.moodle.models import AuthPaths

CONFIG = MoodleBootstrapConfig(
    source_system="moodle:just:student-all-courses",
    canonical_wwwroot="https://elearn.just.edu.jo/current",
    callback_kind="registered_uri",
    callback_scheme="aaos",
    timeout_seconds=5,
)


class FakeLauncher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def open_fixed_auth_url(self, url: str) -> None:
        self.urls.append(url)


class FakeReceiver:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.timeouts: list[int] = []

    def wait_for_callback(self, timeout_seconds: int) -> str:
        self.timeouts.append(timeout_seconds)
        return self.uri


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(
        api_token_path=tmp_path / "credentials" / "moodle" / "just" / "api_token",
        browser_state_dir=tmp_path / "auth" / "moodle" / "just",
    )


def _siteid(passport: str, wwwroot: str = CONFIG.canonical_wwwroot) -> str:
    return hashlib.md5((wwwroot + passport).encode("utf-8")).hexdigest()


def _callback(passport: str, *, web_token: str = "REST-TOKEN", private: str | None = None) -> str:
    envelope = f"{_siteid(passport)}:::{web_token}"
    if private is not None:
        envelope += f":::{private}"
    encoded = base64.b64encode(envelope.encode("utf-8")).decode("ascii")
    return f"aaos://token={encoded}"


def _run_bootstrap(tmp_path: Path, callback: str) -> tuple[Path, FakeLauncher, FakeReceiver]:
    paths = _paths(tmp_path)
    launcher = FakeLauncher()
    receiver = FakeReceiver(callback)
    asyncio.run(bootstrap_token(CONFIG, paths, launcher, receiver))
    return paths.api_token_path, launcher, receiver


def test_registered_uri_bootstrap_extracts_only_web_service_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aaos.moodle.bootstrap.secrets.token_urlsafe", lambda _: "PASSPORT")

    path, launcher, receiver = _run_bootstrap(tmp_path, _callback("PASSPORT", private="PRIVATE"))

    assert path.read_text(encoding="utf-8") == "REST-TOKEN\n"
    assert receiver.timeouts == [5]
    query = parse_qs(urlsplit(launcher.urls[0]).query)
    assert query["service"] == ["moodle_mobile_app"]
    assert query["passport"] == ["PASSPORT"]
    assert query["urlscheme"] == ["aaos"]
    assert "REST-TOKEN" not in launcher.urls[0]


def test_bootstrap_returns_no_secret_value_and_cleans_callback_state(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    launcher = FakeLauncher()
    receiver = FakeReceiver(_callback("wrong-passport"))

    with pytest.raises(BootstrapError) as error:
        asyncio.run(bootstrap_token(CONFIG, paths, launcher, receiver))

    assert str(error.value) == "Moodle authentication bootstrap failed"
    assert "wrong-passport" not in repr(error.value)
    assert not paths.api_token_path.exists()


@pytest.mark.parametrize(
    "uri",
    [
        "http://token=abc",
        "aaos://token=abc/path",
        "aaos://token=abc?extra=1",
        "aaos://token=abc#fragment",
        "aaos://user@token=abc",
        "aaos://token=abc&token=def",
        "aaos://token=",
        "aaos://token=%ZZ",
    ],
)
def test_callback_grammar_rejects_unsafe_uri_shapes(tmp_path: Path, uri: str) -> None:
    with pytest.raises(BootstrapError):
        _run_bootstrap(tmp_path, uri)


def test_callback_rejects_wrong_component_count(tmp_path: Path) -> None:
    envelope = base64.b64encode(b"one:::two:::three:::four").decode("ascii")

    with pytest.raises(BootstrapError):
        _run_bootstrap(tmp_path, f"aaos://token={envelope}")


@pytest.mark.parametrize(
    "encoded",
    ["!not-base64!", base64.b64encode(b":::REST-TOKEN").decode("ascii")],
)
def test_callback_rejects_malformed_or_empty_envelope(tmp_path: Path, encoded: str) -> None:
    with pytest.raises(BootstrapError):
        _run_bootstrap(tmp_path, f"aaos://token={encoded}")


@pytest.mark.parametrize(
    "encoded",
    [
        "A" * (MAX_ENCODED_APP_TOKEN_BYTES + 1),
        base64.b64encode(b"A" * (MAX_DECODED_APP_TOKEN_BYTES + 1)).decode("ascii"),
    ],
)
def test_callback_rejects_encoded_and_decoded_oversize(tmp_path: Path, encoded: str) -> None:
    with pytest.raises(BootstrapError):
        _run_bootstrap(tmp_path, f"aaos://token={encoded}")


def test_bootstrap_config_rejects_non_registered_or_non_aaos_callbacks() -> None:
    with pytest.raises(ValueError):
        replace(CONFIG, callback_kind="loopback_http")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MoodleBootstrapConfig(
            source_system=CONFIG.source_system,
            canonical_wwwroot=CONFIG.canonical_wwwroot,
            callback_kind="registered_uri",
            callback_scheme="moodlemobile",
            timeout_seconds=5,
        )


def test_credential_write_failure_is_fixed_and_secret_free(tmp_path: Path) -> None:
    paths = AuthPaths(
        api_token_path=tmp_path / "directory",
        browser_state_dir=tmp_path / "browser",
    )
    paths.api_token_path.mkdir()

    with pytest.raises(CredentialWriteError) as error:
        from aaos.moodle.auth import write_api_token

        write_api_token(paths, "SECRET-REST-TOKEN")

    assert str(error.value) == "API credential unavailable"
    assert "SECRET-REST-TOKEN" not in repr(error.value)
