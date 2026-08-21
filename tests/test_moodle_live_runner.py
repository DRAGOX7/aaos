from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.moodle.api import MoodleApiError
from aaos.moodle.api_parsing import SafeSiteInfo
from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.embedded_auth import EmbeddedAuthResult, WebView2EmbeddedAuthHost
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import receive_callback

CONFIG = MoodleBootstrapConfig(
    source_system="moodle:just:summer-2026:bootstrap",
    canonical_wwwroot="https://elearn.just.edu.jo/current",
    callback_kind="registered_uri",
    callback_scheme="aaos",
    timeout_seconds=300,
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


class CompletingRuntime:
    def __init__(self, paths: AuthPaths) -> None:
        self.paths = paths
        self.calls = 0

    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        self.calls += 1
        passport = parse_qs(urlsplit(launch_url).query)["passport"][0]
        receive_callback(CONFIG, self.paths, _callback(passport))
        return "CALLBACK_DISPATCHED"


class StaticRuntime:
    def __init__(self, result: EmbeddedAuthResult, *, cleanup_pass: bool = True) -> None:
        self.result = result
        self.cleanup_pass = cleanup_pass
        self.calls = 0

    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        self.calls += 1
        return self.result


class FakeSiteInfoTransport:
    def __init__(self, authorization: object) -> None:
        self.authorization = authorization
        self.calls = 0
        self.closed = False

    async def get_site_info(self) -> SafeSiteInfo:
        self.calls += 1
        self.authorization.consume()  # type: ignore[attr-defined]
        return SafeSiteInfo(
            canonical_wwwroot="https://elearn.just.edu.jo/current",
            release="4.5.0",
            available_functions=(
                "core_webservice_get_site_info",
                "core_course_get_courses",
                "mod_assign_get_assignments",
                "private_function_not_relevant",
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


class FailingSiteInfoTransport:
    def __init__(self, authorization: object) -> None:
        self.authorization = authorization
        self.calls = 0
        self.closed = False

    async def get_site_info(self) -> SafeSiteInfo:
        self.calls += 1
        self.authorization.consume()  # type: ignore[attr-defined]
        raise MoodleApiError("Moodle site-info request failed")

    async def aclose(self) -> None:
        self.closed = True


def test_runner_completes_one_bound_attempt_and_renders_only_safe_site_info(
    tmp_path: Path,
) -> None:
    try:
        from aaos.moodle.live_runner import MoodleLiveBootstrapRunner, render_live_report
    except ModuleNotFoundError:
        pytest.fail("the approved Moodle live runner is absent")

    paths = _paths(tmp_path)
    runtime = CompletingRuntime(paths)
    transport: FakeSiteInfoTransport | None = None

    def transport_factory(authorization: object) -> FakeSiteInfoTransport:
        nonlocal transport
        transport = FakeSiteInfoTransport(authorization)
        return transport

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=transport_factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    rendered = render_live_report(report)

    assert runtime.calls == 1
    assert transport is not None
    assert transport.calls == 1
    assert transport.closed is True
    assert report.exit_code == 0
    assert rendered.splitlines() == [
        "EMBEDDED_SSO=PASS",
        "CALLBACK_RECEIVED=PASS",
        "TOKEN_PERSISTED=PASS",
        "WEBVIEW2_CLEANUP=PASS",
        "SITE_INFO=PASS",
        "CANONICAL_WWWROOT=https://elearn.just.edu.jo/current",
        "MOODLE_RELEASE=4.5.0",
        "RELEVANT_AVAILABLE_FUNCTIONS:",
        "core_webservice_get_site_info",
        "core_course_get_courses",
        "mod_assign_get_assignments",
        "NOTES:",
        "SITE_INFO_ONLY",
        "FUNCTION_AVAILABILITY_DOES_NOT_AUTHORIZE_ENTITY_CALLS",
        "NO_ENTITY_API_OR_DATABASE_SYNC",
    ]
    assert "PRIVATE-REST-TOKEN" not in rendered
    assert "aaos://token=" not in rendered
    assert "private_function_not_relevant" not in rendered


def test_runner_does_not_read_credential_before_constructing_site_info_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.live_runner as live_runner

    class PrematureCredentialReader:
        def __init__(self, _: AuthPaths) -> None:
            pass

        def read_api_token(self) -> str:
            raise AssertionError("credential read must be owned by the validated transport")

    monkeypatch.setattr(live_runner, "FileMoodleCredentialReader", PrematureCredentialReader)
    paths = _paths(tmp_path)
    transport: FakeSiteInfoTransport | None = None

    def transport_factory(authorization: object) -> FakeSiteInfoTransport:
        nonlocal transport
        transport = FakeSiteInfoTransport(authorization)
        return transport

    report = live_runner.MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(CompletingRuntime(paths)),
        site_info_transport_factory=transport_factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert transport is not None
    assert transport.calls == 1
    assert report.exit_code == 0


@pytest.mark.parametrize("result", ["USER_CANCELLED", "TIMED_OUT", "HOST_ERROR"])
def test_runner_stops_before_callback_or_site_info_when_auth_host_fails(
    tmp_path: Path, result: EmbeddedAuthResult
) -> None:
    from aaos.moodle.live_runner import MoodleLiveBootstrapRunner

    runtime = StaticRuntime(result)
    factory_calls = 0

    def factory(_: object) -> FakeSiteInfoTransport:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("site-info must not be created after an auth failure")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert runtime.calls == 1
    assert factory_calls == 0
    assert report.exit_code == 1
    assert report.callback_received == "FAIL"
    assert report.site_info == "FAIL"


def test_runner_fails_closed_when_callback_completion_is_absent(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import MoodleLiveBootstrapRunner

    runtime = StaticRuntime("CALLBACK_DISPATCHED")

    def factory(_: object) -> FakeSiteInfoTransport:
        raise AssertionError("site-info must not run without same-attempt completion")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.embedded_sso == "PASS"
    assert report.callback_received == "FAIL"
    assert report.site_info == "FAIL"


def test_runner_stops_before_site_info_when_host_cleanup_fails(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import MoodleLiveBootstrapRunner

    runtime = StaticRuntime("CALLBACK_DISPATCHED", cleanup_pass=False)

    def factory(_: object) -> FakeSiteInfoTransport:
        raise AssertionError("site-info must not run after WebView2 cleanup failure")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.webview2_cleanup == "FAIL"
    assert report.site_info == "FAIL"


def test_runner_fails_closed_on_site_info_failure_without_entity_fallback(tmp_path: Path) -> None:
    from aaos.moodle.live_runner import MoodleLiveBootstrapRunner

    paths = _paths(tmp_path)
    runtime = CompletingRuntime(paths)
    transport: FailingSiteInfoTransport | None = None

    def factory(authorization: object) -> FailingSiteInfoTransport:
        nonlocal transport
        transport = FailingSiteInfoTransport(authorization)
        return transport

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert transport is not None
    assert transport.calls == 1
    assert transport.closed is True
    assert report.token_persisted == "PASS"
    assert report.site_info == "FAIL"
