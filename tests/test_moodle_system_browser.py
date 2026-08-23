from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.moodle.api_parsing import SafeSiteInfo
from aaos.moodle.bootstrap import (
    CALLBACK_DELIVERY_GRACE_SECONDS,
    WEBVIEW2_INTERACTION_TIMEOUT_SECONDS,
    MoodleBootstrapConfig,
)
from aaos.moodle.live_runner import (
    SYSTEM_BROWSER_COMPLETION_WAIT_SECONDS,
    MoodleSystemBrowserBootstrapRunner,
    render_live_report,
)
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import (
    BootstrapCompletion,
    PendingBootstrapAttempt,
    ReceiverError,
    begin_bootstrap_attempt,
    receive_callback,
    wait_for_bootstrap_completion,
)
from aaos.moodle.system_browser import SystemBrowserAuthHost, SystemBrowserLaunchError

CONFIG = MoodleBootstrapConfig(
    source_system="moodle:just:summer-2026:bootstrap",
    canonical_wwwroot="https://elearn.just.edu.jo/current",
    callback_kind="registered_uri",
    callback_scheme="aaos",
    timeout_seconds=600,
)
SETTINGS = MoodleSettings(
    source_system=CONFIG.source_system,
    base_origin="https://elearn.just.edu.jo",
    mode="api",
    auth_alias="just-summer-2026",
)


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(
        api_token_path=tmp_path / "credentials" / "moodle" / "just-summer-2026" / "api_token",
        browser_state_dir=tmp_path / "auth" / "moodle" / "just-summer-2026",
    )


def _callback(passport: str) -> str:
    site_id = hashlib.md5((CONFIG.canonical_wwwroot + passport).encode("utf-8")).hexdigest()
    envelope = base64.b64encode(f"{site_id}:::PRIVATE-REST-TOKEN".encode("utf-8")).decode("ascii")
    return f"aaos://token={envelope}"


class FakeSiteInfoTransport:
    def __init__(self, authorization: object) -> None:
        self.authorization = authorization
        self.calls = 0

    async def get_site_info(self) -> SafeSiteInfo:
        self.calls += 1
        self.authorization.consume()  # type: ignore[attr-defined]
        return SafeSiteInfo(
            canonical_wwwroot=CONFIG.canonical_wwwroot,
            release="4.5.0",
            available_functions=("core_webservice_get_site_info",),
        )

    async def aclose(self) -> None:
        return None


def test_system_browser_host_opens_only_the_fixed_moodle_mobile_url() -> None:
    urls: list[str] = []
    host = SystemBrowserAuthHost(urls.append)

    host.start(CONFIG.canonical_wwwroot + "/admin/tool/mobile/launch.php", "p" * 32)

    assert len(urls) == 1
    parsed = urlsplit(urls[0])
    assert parsed.scheme == "https"
    assert parsed.hostname == "elearn.just.edu.jo"
    assert parsed.path == "/current/admin/tool/mobile/launch.php"
    assert parse_qs(parsed.query) == {
        "service": ["moodle_mobile_app"],
        "passport": ["p" * 32],
        "urlscheme": ["aaos"],
    }
    assert "oauthsso" not in urls[0]


def test_system_browser_host_rejects_an_arbitrary_url_without_launching() -> None:
    urls: list[str] = []
    host = SystemBrowserAuthHost(urls.append)

    with pytest.raises(ValueError):
        host.start("https://example.com/admin/tool/mobile/launch.php", "p" * 32)

    assert urls == []


def test_system_browser_runner_requires_receiver_completion_before_site_info(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    urls: list[str] = []
    transport: FakeSiteInfoTransport | None = None

    def launch(url: str) -> None:
        urls.append(url)
        passport = parse_qs(urlsplit(url).query)["passport"][0]
        receive_callback(CONFIG, paths, _callback(passport))

    def factory(authorization: object) -> FakeSiteInfoTransport:
        nonlocal transport
        transport = FakeSiteInfoTransport(authorization)
        return transport

    report = MoodleSystemBrowserBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=SystemBrowserAuthHost(launch),
        site_info_transport_factory=factory,
        completion_timeout_seconds=1,
    ).run()

    assert len(urls) == 1
    assert transport is not None and transport.calls == 1
    assert report.system_browser == "PASS"
    assert report.embedded_sso == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "NOT_ATTEMPTED"
    assert report.callback_received == "PASS"
    assert report.token_persisted == "PASS"
    assert report.site_info == "PASS"
    assert report.exit_code == 0


def test_system_browser_launch_failure_stops_before_callback_and_site_info(tmp_path: Path) -> None:
    class FailingOpener:
        def __call__(self, _: str) -> None:
            raise OSError("private browser failure")

    report = MoodleSystemBrowserBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=SystemBrowserAuthHost(FailingOpener()),
        site_info_transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("must not run")),
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "SYSTEM_BROWSER_LAUNCH"
    assert report.system_browser == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "NOT_ATTEMPTED"
    assert report.site_info == "NOT_ATTEMPTED"
    assert report.exit_code == 1


def test_system_browser_runner_rejects_a_missing_or_mismatched_receiver_completion(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    launches = 0

    def launch(_: str) -> None:
        nonlocal launches
        launches += 1

    def reject_mismatched_completion(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        assert paths == _paths(tmp_path)
        assert attempt.source_system == CONFIG.source_system
        assert timeout_seconds == 60
        raise ReceiverError("PRIVATE CALLBACK DETAIL", failure_stage="RECEIVER_REJECTED")

    report = MoodleSystemBrowserBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=SystemBrowserAuthHost(launch),
        site_info_transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("must not run")),
        completion_timeout_seconds=60,
        completion_waiter=reject_mismatched_completion,
    ).run()

    rendered = render_live_report(report)
    assert launches == 1
    assert report.failure_stage == "RECEIVER_REJECTED"
    assert report.system_browser == "PASS"
    assert report.callback_received == "FAIL"
    assert report.site_info == "NOT_ATTEMPTED"
    assert "PRIVATE CALLBACK DETAIL" not in rendered
    assert "PRIVATE-REST-TOKEN" not in rendered


def test_system_browser_runner_uses_the_bounded_callback_wait_timeout(tmp_path: Path) -> None:
    captured: dict[str, int] = {}

    def wait(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        assert paths == _paths(tmp_path)
        assert attempt.source_system == CONFIG.source_system
        captured["timeout_seconds"] = timeout_seconds
        raise ReceiverError(
            "Moodle callback receiver failed", failure_stage="CALLBACK_NOT_RECEIVED"
        )

    report = MoodleSystemBrowserBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=SystemBrowserAuthHost(lambda _: None),
        site_info_transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("must not run")),
        completion_timeout_seconds=60,
        completion_waiter=wait,
    ).run()

    assert captured == {"timeout_seconds": 60}
    assert report.failure_stage == "CALLBACK_NOT_RECEIVED"
    assert report.site_info == "NOT_ATTEMPTED"


def test_system_browser_wait_stops_at_absolute_attempt_expiry_before_its_nominal_deadline(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    with pytest.raises(ReceiverError) as error:
        wait_for_bootstrap_completion(
            paths,
            attempt,
            timeout_seconds=SYSTEM_BROWSER_COMPLETION_WAIT_SECONDS,
            clock=lambda: float(attempt.expires_at + 1),
            monotonic=lambda: 0.0,
            sleeper=lambda _: pytest.fail("must not sleep past attempt expiry"),
        )

    assert error.value.failure_stage == "CALLBACK_NOT_RECEIVED"


def test_configured_system_mode_does_not_construct_a_webview2_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aaos.moodle import live_runner

    captured: dict[str, object] = {}

    class CapturingRunner:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> object:
            return live_runner._failed_report(
                failure_stage="SYSTEM_BROWSER_LAUNCH",
                auth_host="system",
                system_browser="FAIL",
            )

    def must_not_construct(_: object) -> object:
        raise AssertionError("system mode must not construct WebView2")

    monkeypatch.setattr(
        live_runner,
        "load_runtime_moodle_configuration",
        lambda: (CONFIG, SETTINGS, _paths(tmp_path)),
    )
    monkeypatch.setattr(live_runner, "MoodleSystemBrowserBootstrapRunner", CapturingRunner)
    monkeypatch.setattr(live_runner, "NativeWebView2Runtime", must_not_construct)

    assert live_runner.run_configured_moodle_bootstrap(auth_host="system") == 1
    assert isinstance(captured["auth_host"], SystemBrowserAuthHost)
    assert SYSTEM_BROWSER_COMPLETION_WAIT_SECONDS == (
        WEBVIEW2_INTERACTION_TIMEOUT_SECONDS + CALLBACK_DELIVERY_GRACE_SECONDS
    )
    assert captured["completion_timeout_seconds"] == SYSTEM_BROWSER_COMPLETION_WAIT_SECONDS
    assert captured["completion_timeout_seconds"] != CALLBACK_DELIVERY_GRACE_SECONDS


def test_system_browser_host_uses_a_fixed_error_for_opener_failure() -> None:
    def fail(_: str) -> None:
        raise OSError("PRIVATE URI OR TOKEN")

    with pytest.raises(SystemBrowserLaunchError) as error:
        SystemBrowserAuthHost(fail).start(
            CONFIG.canonical_wwwroot + "/admin/tool/mobile/launch.php", "p" * 32
        )

    assert str(error.value) == "System browser authentication launch failed"
    assert "PRIVATE URI OR TOKEN" not in repr(error.value)
