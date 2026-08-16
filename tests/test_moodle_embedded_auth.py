from __future__ import annotations

from pathlib import Path

import pytest

from aaos.moodle.embedded_auth import (
    EmbeddedAuthResult,
    EphemeralProfile,
    WebView2EmbeddedAuthHost,
    WebView2RuntimeUnavailable,
    build_embedded_launch_url,
    is_allowed_external_scheme,
    is_allowed_new_window,
)

LAUNCH_URL = "https://elearn.just.edu.jo/current/admin/tool/mobile/launch.php"
PASSPORT = "p" * 32


class FakeRuntime:
    def __init__(self, result: EmbeddedAuthResult = "CALLBACK_DISPATCHED") -> None:
        self.result = result
        self.calls: list[tuple[str, Path, int]] = []

    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        self.calls.append((launch_url, profile, timeout_seconds))
        return self.result


class RaisingRuntime:
    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        raise RuntimeError("runtime failure")


def test_launch_contract_contains_exact_parameters() -> None:
    result = build_embedded_launch_url(LAUNCH_URL, PASSPORT)
    assert result.startswith(LAUNCH_URL + "?")
    assert "service=moodle_mobile_app" in result
    assert "passport=" + PASSPORT in result
    assert "urlscheme=aaos" in result
    assert "oauthsso" not in result


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/current/admin/tool/mobile/launch.php",
        "https://elearn.just.edu.jo:444/current/admin/tool/mobile/launch.php",
        LAUNCH_URL + "?extra=1",
    ],
)
def test_launch_contract_rejects_unapproved_url(url: str) -> None:
    with pytest.raises(ValueError):
        build_embedded_launch_url(url, PASSPORT)


def test_ephemeral_profiles_are_unique_and_cleaned(tmp_path: Path) -> None:
    first = EphemeralProfile.create(tmp_path)
    second = EphemeralProfile.create(tmp_path)
    assert first.path != second.path
    assert first.path.is_dir() and second.path.is_dir()
    first.cleanup()
    second.cleanup()
    assert not first.path.exists() and not second.path.exists()


def test_external_uri_policy_allows_only_aaos() -> None:
    assert is_allowed_external_scheme("aaos") is True
    assert is_allowed_external_scheme("https") is False
    assert is_allowed_external_scheme("other") is False


def test_new_window_policy_keeps_same_origin_https_only() -> None:
    assert is_allowed_new_window("https://elearn.just.edu.jo/current/login") is True
    assert is_allowed_new_window("https://elearn.just.edu.jo:444/current/login") is False
    assert is_allowed_new_window("https://login.microsoftonline.com/authorize") is False
    assert is_allowed_new_window("aaos://token=secret") is False


def test_host_uses_ephemeral_profile_and_returns_only_result(tmp_path: Path) -> None:
    runtime = FakeRuntime("USER_CANCELLED")
    host = WebView2EmbeddedAuthHost(runtime)
    result = host.start(LAUNCH_URL, tmp_path, PASSPORT, 30)
    assert result == "USER_CANCELLED"
    assert len(runtime.calls) == 1
    launch, profile, timeout = runtime.calls[0]
    assert launch.startswith(LAUNCH_URL + "?")
    assert profile.parent == tmp_path
    assert not profile.exists()
    assert timeout == 30


def test_unavailable_runtime_is_fixed_error() -> None:
    with pytest.raises(WebView2RuntimeUnavailable) as error:
        WebView2EmbeddedAuthHost.unavailable().start(LAUNCH_URL, Path("data"), "p", 30)
    assert str(error.value) == "WebView2 embedded authentication is unavailable"


def test_invalid_runtime_result_is_host_error_and_profile_is_cleaned(tmp_path: Path) -> None:
    host = WebView2EmbeddedAuthHost(FakeRuntime("not-a-result"))  # type: ignore[arg-type]
    assert host.start(LAUNCH_URL, tmp_path, PASSPORT, 30) == "HOST_ERROR"
    assert list(tmp_path.iterdir()) == []


def test_runtime_failure_returns_host_error_and_profile_is_cleaned(tmp_path: Path) -> None:
    host = WebView2EmbeddedAuthHost(RaisingRuntime())
    assert host.start(LAUNCH_URL, tmp_path, PASSPORT, 30) == "HOST_ERROR"
    assert list(tmp_path.iterdir()) == []
