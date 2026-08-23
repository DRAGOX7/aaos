from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.embedded_auth import build_embedded_launch_url
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import (
    BootstrapCompletion,
    PendingBootstrapAttempt,
    ReceiverError,
    receive_callback,
)
from aaos.moodle.system_browser import SystemBrowserAuthHost, SystemBrowserLaunchError

CONFIG = MoodleBootstrapConfig(
    source_system="moodle:fixture:confirmed-return",
    canonical_wwwroot="https://elearn.just.edu.jo/current",
    callback_kind="registered_uri",
    callback_scheme="aaos",
    timeout_seconds=600,
)
SETTINGS = MoodleSettings(
    source_system=CONFIG.source_system,
    base_origin="https://elearn.just.edu.jo",
    mode="api",
    auth_alias="confirmed-return",
)


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(
        api_token_path=tmp_path / "credentials" / "moodle" / "confirmed-return" / "api_token",
        browser_state_dir=tmp_path / "auth" / "moodle" / "confirmed-return",
    )


def _callback(passport: str) -> str:
    site_id = hashlib.md5((CONFIG.canonical_wwwroot + passport).encode("utf-8")).hexdigest()
    envelope = base64.b64encode(f"{site_id}:::SYNTHETIC-WS-TOKEN".encode("utf-8")).decode("ascii")
    return f"aaos://token={envelope}"


def test_confirmed_return_browser_host_opens_only_the_fixed_confirmed_url() -> None:
    urls: list[str] = []
    host = SystemBrowserAuthHost(urls.append)

    host.start_confirmed_return(
        CONFIG.canonical_wwwroot + "/admin/tool/mobile/launch.php", "p" * 32
    )

    assert len(urls) == 1
    parsed = urlsplit(urls[0])
    assert parsed.scheme == "https"
    assert parsed.hostname == "elearn.just.edu.jo"
    assert parsed.path == "/current/admin/tool/mobile/launch.php"
    assert parse_qs(parsed.query) == {
        "service": ["moodle_mobile_app"],
        "passport": ["p" * 32],
        "urlscheme": ["aaos"],
        "confirmed": ["1"],
    }
    assert "oauthsso" not in urls[0]


def test_ordinary_system_browser_url_remains_without_confirmed() -> None:
    url = build_embedded_launch_url(
        CONFIG.canonical_wwwroot + "/admin/tool/mobile/launch.php", "p" * 32
    )

    assert "confirmed" not in parse_qs(urlsplit(url).query)


def test_confirmed_return_runner_stops_after_exact_receiver_completion(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import MoodleConfirmedReturnDiagnosticRunner

    paths = _paths(tmp_path)
    urls: list[str] = []

    def launch(url: str) -> None:
        urls.append(url)
        passport = parse_qs(urlsplit(url).query)["passport"][0]
        receive_callback(CONFIG, paths, _callback(passport))

    report = MoodleConfirmedReturnDiagnosticRunner(
        config=CONFIG,
        paths=paths,
        auth_host=SystemBrowserAuthHost(launch),
        completion_timeout_seconds=1,
    ).run()

    assert len(urls) == 1
    assert report.diagnostic == "PASS"
    assert report.system_browser == "PASS"
    assert report.callback_received == "PASS"
    assert report.failure_stage == "NO_FAILURE"
    assert report.exit_code == 0


def test_confirmed_return_receiver_failure_is_private_and_stops(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import (
        MoodleConfirmedReturnDiagnosticRunner,
        render_confirmed_return_diagnostic,
    )

    def reject(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        del paths, attempt
        assert timeout_seconds > 0
        raise ReceiverError("PRIVATE CALLBACK URI TOKEN", failure_stage="RECEIVER_REJECTED")

    report = MoodleConfirmedReturnDiagnosticRunner(
        config=CONFIG,
        paths=_paths(tmp_path),
        auth_host=SystemBrowserAuthHost(lambda _: None),
        completion_timeout_seconds=1,
        completion_waiter=reject,
    ).run()

    rendered = render_confirmed_return_diagnostic(report)
    assert report.diagnostic == "FAIL"
    assert report.system_browser == "PASS"
    assert report.callback_received == "FAIL"
    assert report.failure_stage == "RECEIVER_REJECTED"
    assert "PRIVATE CALLBACK URI TOKEN" not in rendered
    assert "SYNTHETIC-WS-TOKEN" not in rendered


@pytest.mark.parametrize(
    ("receiver_stage", "expected_stage"),
    (
        ("CALLBACK_NOT_RECEIVED", "CALLBACK_NOT_RECEIVED"),
        ("RECEIVER_REJECTED", "RECEIVER_REJECTED"),
    ),
)
def test_confirmed_return_receiver_failures_stop_before_any_follow_on_operation(
    tmp_path: Path,
    receiver_stage: Literal["CALLBACK_NOT_RECEIVED", "RECEIVER_REJECTED"],
    expected_stage: Literal["CALLBACK_NOT_RECEIVED", "RECEIVER_REJECTED"],
) -> None:
    from aaos.moodle.live_runner import MoodleConfirmedReturnDiagnosticRunner

    def fail_after_exact_wait(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        del paths, attempt
        assert timeout_seconds == 660
        raise ReceiverError("private failure", failure_stage=receiver_stage)

    report = MoodleConfirmedReturnDiagnosticRunner(
        config=CONFIG,
        paths=_paths(tmp_path),
        auth_host=SystemBrowserAuthHost(lambda _: None),
        completion_timeout_seconds=660,
        completion_waiter=fail_after_exact_wait,
    ).run()

    assert report.diagnostic == "FAIL"
    assert report.system_browser == "PASS"
    assert report.callback_received == "FAIL"
    assert report.failure_stage == expected_stage


def test_confirmed_return_browser_failure_does_not_wait_for_callback(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import MoodleConfirmedReturnDiagnosticRunner

    def fail(_: str) -> None:
        raise SystemBrowserLaunchError()

    def must_not_wait(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        del paths, attempt, timeout_seconds
        raise AssertionError("callback wait must not run after browser launch failure")

    report = MoodleConfirmedReturnDiagnosticRunner(
        config=CONFIG,
        paths=_paths(tmp_path),
        auth_host=SystemBrowserAuthHost(fail),
        completion_timeout_seconds=1,
        completion_waiter=must_not_wait,
    ).run()

    assert report.diagnostic == "FAIL"
    assert report.system_browser == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.failure_stage == "SYSTEM_BROWSER_LAUNCH"


def test_configured_confirmed_return_diagnostic_never_constructs_site_info_or_webview2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aaos.moodle import live_runner

    captured_timeout_seconds: int | None = None

    class CapturingRunner:
        def __init__(self, **kwargs: object) -> None:
            nonlocal captured_timeout_seconds
            assert isinstance(kwargs["auth_host"], SystemBrowserAuthHost)
            assert isinstance(kwargs["completion_timeout_seconds"], int)
            captured_timeout_seconds = kwargs["completion_timeout_seconds"]

        def run(self) -> object:
            return live_runner.ConfirmedReturnDiagnosticReport(
                failure_stage="SYSTEM_BROWSER_LAUNCH",
                diagnostic="FAIL",
                system_browser="FAIL",
                callback_received="NOT_ATTEMPTED",
            )

    def must_not_construct(*_: object, **__: object) -> object:
        raise AssertionError("diagnostic must not construct this boundary")

    monkeypatch.setattr(
        live_runner,
        "load_runtime_moodle_configuration",
        lambda: (CONFIG, SETTINGS, _paths(tmp_path)),
    )
    monkeypatch.setattr(live_runner, "MoodleConfirmedReturnDiagnosticRunner", CapturingRunner)
    monkeypatch.setattr(live_runner, "MoodleApiTransport", must_not_construct)
    monkeypatch.setattr(live_runner, "SingleUseSiteInfoProbeAuthorization", must_not_construct)
    monkeypatch.setattr(live_runner, "FileMoodleCredentialReader", must_not_construct)
    monkeypatch.setattr(live_runner, "NativeWebView2Runtime", must_not_construct)

    assert live_runner.run_configured_moodle_confirmed_return_diagnostic() == 1
    assert captured_timeout_seconds == 660
