from __future__ import annotations

from pathlib import Path

import pytest

from aaos.moodle.auth import (
    MAX_API_TOKEN_BYTES,
    BrowserStateError,
    CredentialReadError,
    load_browser_state_path,
    read_api_token,
)
from aaos.moodle.models import MoodleSettings, resolve_auth_paths

SETTINGS = MoodleSettings(
    source_system="moodle:university-primary:student-all-courses",
    base_origin="https://moodle.example.edu",
    mode="api",
    auth_alias="university-primary",
)


def test_resolve_auth_paths_is_confined_to_approved_roots(tmp_path: Path) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)

    assert (
        paths.api_token_path
        == tmp_path / "credentials" / "moodle" / "university-primary" / "api_token"
    )
    assert paths.browser_state_dir == tmp_path / "auth" / "moodle" / "university-primary"
    assert not paths.api_token_path.exists()
    assert not paths.browser_state_dir.exists()


def test_read_api_token_uses_bounded_strict_file_read(tmp_path: Path) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)
    paths.api_token_path.parent.mkdir(parents=True)
    paths.api_token_path.write_bytes(b"synthetic-token\n")

    assert read_api_token(paths) == "synthetic-token"


@pytest.mark.parametrize(
    "payload",
    [b"", b"   \n", b"x" * (MAX_API_TOKEN_BYTES + 1), b"\xff"],
)
def test_read_api_token_rejects_missing_or_malformed_state(tmp_path: Path, payload: bytes) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)
    paths.api_token_path.parent.mkdir(parents=True)
    paths.api_token_path.write_bytes(payload)

    with pytest.raises(CredentialReadError) as error:
        read_api_token(paths)

    assert "synthetic-token" not in str(error.value)


def test_read_api_token_rejects_missing_file_without_exposing_path(tmp_path: Path) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)

    with pytest.raises(CredentialReadError) as error:
        read_api_token(paths)

    assert str(paths.api_token_path) not in str(error.value)


def test_browser_state_loader_accepts_only_dedicated_directory(tmp_path: Path) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)
    paths.browser_state_dir.mkdir(parents=True)
    state = paths.browser_state_dir / "storage_state.json"
    state.write_text('{"origins":[{"origin":"https://moodle.example.edu"}]}', encoding="utf-8")

    assert load_browser_state_path(paths, SETTINGS.base_origin) == state


def test_browser_state_loader_rejects_unexpected_origin(tmp_path: Path) -> None:
    paths = resolve_auth_paths(SETTINGS, data_root=tmp_path)
    paths.browser_state_dir.mkdir(parents=True)
    (paths.browser_state_dir / "storage_state.json").write_text(
        '{"origins":[{"origin":"https://moodle.example.edu"}]}', encoding="utf-8"
    )

    with pytest.raises(BrowserStateError):
        load_browser_state_path(paths, "https://other.example.edu")
