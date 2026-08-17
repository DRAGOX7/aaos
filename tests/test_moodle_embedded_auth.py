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
from aaos.moodle.native_webview2 import NativeWebView2Runtime

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


def test_native_runtime_sends_sensitive_launch_data_only_on_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> object:
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return type("Completed", (), {"stdout": b"CALLBACK_DISPATCHED\n"})()

    monkeypatch.setattr("aaos.moodle.native_webview2.subprocess.run", fake_run)
    helper = NativeWebView2Runtime(Path("C:/AAOS/AAOS.WebView2Host.exe"))
    assert helper.run(LAUNCH_URL + "?passport=secret", tmp_path, 30) == "CALLBACK_DISPATCHED"
    assert captured["args"] == [str(Path("C:/AAOS/AAOS.WebView2Host.exe"))]
    assert b"passport=secret" in captured["input"]  # type: ignore[operator]


def test_native_helper_tracks_browser_exit_before_profile_cleanup() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "BrowserProcessId" in source
    assert "BrowserProcessExited" in source
    assert "WaitForBrowserProcessExit" in source
    assert "CleanupResult" in source
    assert "Directory.Delete(_profilePath, recursive: true)" not in source


def test_native_helper_reports_dispatch_only_after_exact_external_event() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert (
        "data:text/html,<a id='aaos' href='aaos://invalid'>continue</a><script>document.getElementById('aaos').click()</script>"
        in source
    )
    assert "AAOS_EXTERNAL_SCHEME_EVENT_SEEN" in source
    assert "args.Cancel = false" in source
    assert 'Result = "CALLBACK_DISPATCHED"' in source


def test_native_helper_cannot_deadlock_ui_thread_during_terminal_cleanup() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "ConfigureAwait(false)" in source
    assert "Console.Out.Flush()" in source
    assert "Application.ExitThread()" in source


def test_native_helper_constrains_profile_cleanup_to_attempt_temp_root() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "Path.GetTempPath()" in source
    assert "embedded-" in source
    assert "IsApprovedProfilePath" in source
    assert "service=moodle_mobile_app" in source
    assert "urlscheme=aaos" in source


def test_callback_interaction_result_survives_parent_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = FakeRuntime("CALLBACK_DISPATCHED")

    def blocked_cleanup(self: EphemeralProfile) -> None:
        raise WebView2RuntimeUnavailable()

    monkeypatch.setattr(EphemeralProfile, "cleanup", blocked_cleanup)
    result = WebView2EmbeddedAuthHost(runtime).start(LAUNCH_URL, tmp_path, PASSPORT, 30)
    assert result == "HOST_ERROR"
    assert runtime.calls[0][0].startswith(LAUNCH_URL + "?")


def test_native_cleanup_failure_still_fails_closed_without_erasing_interaction(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime("CALLBACK_DISPATCHED")
    runtime.cleanup_pass = False  # type: ignore[attr-defined]
    result = WebView2EmbeddedAuthHost(runtime).start(LAUNCH_URL, tmp_path, PASSPORT, 30)
    assert result == "HOST_ERROR"


def test_native_helper_has_diagnostic_only_failure_stage_vocabulary() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    for stage in (
        "WEBVIEW2_ENVIRONMENT_INIT_FAILED",
        "CORE_WEBVIEW2_INIT_FAILED",
        "INITIAL_NAVIGATION_FAILED",
        "EXTERNAL_SCHEME_EVENT_NOT_SEEN",
        "BROWSER_SHUTDOWN_TIMEOUT",
        "UNEXPECTED_RUNTIME_FAILURE",
        "INPUT_DESERIALIZATION_FAILED",
        "PROFILE_INITIALIZATION_FAILED",
        "EVENT_SUBSCRIPTION_FAILED",
        "SHUTDOWN_FAILED",
    ):
        assert stage in source
    assert "Diagnostic" in source
    assert "FAILURE_STAGE=" in source
    assert source.count('_failureStage = "UNEXPECTED_RUNTIME_FAILURE"') == 1


def test_native_helper_is_console_ipc_and_has_no_webview_self_test() -> None:
    project = Path("native/AAOS.WebView2Host/AAOS.WebView2Host.csproj").read_text(encoding="utf-8")
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "<OutputType>Exe</OutputType>" in project
    assert "SelfTest" in source
    assert "IPC_SELF_TEST" in source
    assert 'WriteStatus("HOST_ERROR", 1, null, true, "IPC_SELF_TEST")' in source


def test_native_helper_leaves_profile_cleanup_to_parent() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "Directory.Delete(_profilePath, recursive: true)" not in source
    assert "CleanupResult" in source


def test_native_shutdown_releases_browser_before_form_closes() -> None:
    source = Path("native/AAOS.WebView2Host/Program.cs").read_text(encoding="utf-8")
    assert "FormClosing += OnFormClosing" in source
    assert "BeginShutdown" in source
    assert "_allowClose" in source
    assert "_coreWebView2 = null" in source
    assert "if (_allowClose)" in source
    assert "args.Cancel = true" in source
