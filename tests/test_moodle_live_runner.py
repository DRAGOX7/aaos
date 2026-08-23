from __future__ import annotations

import base64
import hashlib
import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.moodle import live_runner
from aaos.moodle.api import MoodleApiError, MoodleApiFailureStage
from aaos.moodle.api_parsing import SafeSiteInfo
from aaos.moodle.bootstrap import CALLBACK_DELIVERY_GRACE_SECONDS, MoodleBootstrapConfig
from aaos.moodle.embedded_auth import (
    EmbeddedAuthResult,
    EphemeralProfile,
    WebView2EmbeddedAuthHost,
)
from aaos.moodle.live_runner import MoodleLiveBootstrapRunner, render_live_report
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import (
    BootstrapCompletion,
    PendingBootstrapAttempt,
    ReceiverError,
    ReceiverFailureStage,
    receive_callback,
)

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
    def __init__(
        self,
        result: EmbeddedAuthResult,
        *,
        cleanup_pass: bool = True,
        pre_initialization_failure: bool = False,
    ) -> None:
        self.result = result
        self.cleanup_pass = cleanup_pass
        self.pre_initialization_failure = pre_initialization_failure
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


class StagedSiteInfoTransport:
    def __init__(self, failure_stage: MoodleApiFailureStage) -> None:
        self.failure_stage = failure_stage
        self.closed = False

    async def get_site_info(self) -> SafeSiteInfo:
        raise MoodleApiError("fixed safe site-info failure", failure_stage=self.failure_stage)

    async def aclose(self) -> None:
        self.closed = True


def test_runner_completes_one_bound_attempt_and_renders_only_safe_site_info(
    tmp_path: Path,
) -> None:
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
        "FAILURE_STAGE=NO_FAILURE",
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


@pytest.mark.parametrize(
    ("result", "failure_stage"),
    [
        ("USER_CANCELLED", "CALLBACK_NOT_RECEIVED"),
        ("TIMED_OUT", "WEBVIEW2_TIMEOUT"),
        ("HOST_ERROR", "WEBVIEW2_HOST_ERROR"),
    ],
)
def test_runner_reports_the_specific_auth_failure_without_starting_downstream_stages(
    tmp_path: Path, result: EmbeddedAuthResult, failure_stage: str
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
    assert report.failure_stage == failure_stage
    assert report.embedded_sso == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.site_info == "NOT_ATTEMPTED"


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
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "PASS"
    assert report.site_info == "NOT_ATTEMPTED"
    assert report.failure_stage == "CALLBACK_NOT_RECEIVED"


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

    assert report.failure_stage == "WEBVIEW2_CLEANUP"
    assert report.embedded_sso == "PASS"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "FAIL"
    assert report.site_info == "NOT_ATTEMPTED"


def test_runner_reports_preinitialization_helper_rejection_as_webview2_launch(
    tmp_path: Path,
) -> None:
    runtime = StaticRuntime("HOST_ERROR", pre_initialization_failure=True)

    def factory(_: object) -> FakeSiteInfoTransport:
        raise AssertionError("site-info must not run after helper startup rejection")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(runtime),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "WEBVIEW2_LAUNCH"
    assert report.embedded_sso == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "PASS"
    assert report.site_info == "NOT_ATTEMPTED"


def test_configured_bootstrap_profiles_use_the_native_temp_root_contract() -> None:
    profile_root = live_runner._configured_profile_root()

    assert profile_root == Path(tempfile.gettempdir()).resolve()
    first = EphemeralProfile.create(profile_root)
    second = EphemeralProfile.create(profile_root)
    try:
        assert first.path.parent == profile_root
        assert second.path.parent == profile_root
        assert first.path != second.path
    finally:
        first.cleanup()
        second.cleanup()


def test_configured_bootstrap_limits_callback_wait_to_delivery_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class CapturingRunner:
        def __init__(self, **kwargs: object) -> None:
            captured["completion_timeout_seconds"] = kwargs["completion_timeout_seconds"]

        def run(self) -> object:
            return live_runner._failed_report(failure_stage="WEBVIEW2_LAUNCH")

    monkeypatch.setattr(
        live_runner,
        "load_runtime_moodle_configuration",
        lambda: (CONFIG, SETTINGS, _paths(tmp_path)),
    )
    monkeypatch.setattr(live_runner, "MoodleLiveBootstrapRunner", CapturingRunner)

    assert live_runner.run_configured_moodle_bootstrap() == 1
    assert captured["completion_timeout_seconds"] == CALLBACK_DELIVERY_GRACE_SECONDS


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
    assert report.failure_stage == "SITE_INFO_REQUEST"


def test_runner_reports_attempt_creation_failure_before_webview2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.live_runner as live_runner

    def reject_attempt(*_: object, **__: object) -> object:
        raise ReceiverError("Moodle callback receiver failed")

    monkeypatch.setattr(live_runner, "begin_bootstrap_attempt", reject_attempt)
    report = live_runner.MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(StaticRuntime("HOST_ERROR")),
        site_info_transport_factory=lambda _: FakeSiteInfoTransport(object()),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "ATTEMPT_CREATION"
    assert report.embedded_sso == "NOT_ATTEMPTED"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "NOT_ATTEMPTED"
    assert report.site_info == "NOT_ATTEMPTED"


def test_runner_reports_webview2_launch_failure_without_claiming_cleanup(tmp_path: Path) -> None:
    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost.unavailable(),
        site_info_transport_factory=lambda _: FakeSiteInfoTransport(object()),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "WEBVIEW2_LAUNCH"
    assert report.embedded_sso == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "NOT_ATTEMPTED"
    assert report.site_info == "NOT_ATTEMPTED"


def test_runner_reports_profile_initialization_failure_as_webview2_launch(tmp_path: Path) -> None:
    profile_root = tmp_path / "not-a-directory"
    profile_root.write_text("not a directory", encoding="utf-8")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(StaticRuntime("HOST_ERROR")),
        site_info_transport_factory=lambda _: FakeSiteInfoTransport(object()),
        profile_root=profile_root,
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "WEBVIEW2_LAUNCH"
    assert report.embedded_sso == "FAIL"
    assert report.callback_received == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "NOT_ATTEMPTED"


def test_runner_reports_receiver_rejection_after_webview2_completion(
    tmp_path: Path,
) -> None:
    def reject_completion(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        assert paths == _paths(tmp_path)
        assert attempt.source_system == CONFIG.source_system
        assert timeout_seconds == 1
        raise ReceiverError("Moodle callback receiver failed", failure_stage="RECEIVER_REJECTED")

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(StaticRuntime("CALLBACK_DISPATCHED")),
        site_info_transport_factory=lambda _: FakeSiteInfoTransport(object()),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
        completion_waiter=reject_completion,
    ).run()

    assert report.failure_stage == "RECEIVER_REJECTED"
    assert report.embedded_sso == "PASS"
    assert report.callback_received == "FAIL"
    assert report.token_persisted == "NOT_ATTEMPTED"
    assert report.webview2_cleanup == "PASS"
    assert report.site_info == "NOT_ATTEMPTED"


@pytest.mark.parametrize(
    "failure_stage",
    [
        "CREDENTIAL_NOT_PERSISTED",
        "SITE_INFO_AUTHORIZATION",
        "SITE_INFO_REQUEST",
        "SITE_INFO_PARSE",
    ],
)
def test_runner_reports_typed_site_info_failure_without_exposing_its_message(
    tmp_path: Path, failure_stage: MoodleApiFailureStage
) -> None:
    paths = _paths(tmp_path)

    def factory(_: object) -> StagedSiteInfoTransport:
        return StagedSiteInfoTransport(failure_stage)

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(CompletingRuntime(paths)),
        site_info_transport_factory=factory,
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()
    rendered = render_live_report(report)

    assert report.failure_stage == failure_stage
    assert report.embedded_sso == "PASS"
    assert report.callback_received == "PASS"
    assert report.token_persisted == "PASS"
    assert report.webview2_cleanup == "PASS"
    assert report.site_info == "FAIL"
    assert "fixed safe site-info failure" not in rendered
    assert "PRIVATE-REST-TOKEN" not in rendered


def test_runner_normalizes_an_invalid_site_info_failure_stage_without_rendering_it(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    class InvalidStageTransport:
        async def get_site_info(self) -> SafeSiteInfo:
            raise MoodleApiError(
                "PRIVATE SITE-INFO DETAIL",
                failure_stage=cast(MoodleApiFailureStage, "PRIVATE_SITE_INFO_STAGE"),
            )

        async def aclose(self) -> None:
            return None

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(CompletingRuntime(paths)),
        site_info_transport_factory=lambda _: InvalidStageTransport(),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()
    rendered = render_live_report(report)

    assert report.failure_stage == "SITE_INFO_REQUEST"
    assert "PRIVATE SITE-INFO DETAIL" not in rendered
    assert "PRIVATE_SITE_INFO_STAGE" not in rendered


def test_runner_normalizes_an_invalid_receiver_failure_stage_without_rendering_it(
    tmp_path: Path,
) -> None:
    def reject_completion(
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        assert paths == _paths(tmp_path)
        assert attempt.source_system == CONFIG.source_system
        assert timeout_seconds == 1
        raise ReceiverError(
            "PRIVATE RECEIVER DETAIL",
            failure_stage=cast(ReceiverFailureStage, "PRIVATE_RECEIVER_STAGE"),
        )

    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=_paths(tmp_path),
        auth_host=WebView2EmbeddedAuthHost(StaticRuntime("CALLBACK_DISPATCHED")),
        site_info_transport_factory=lambda _: FakeSiteInfoTransport(object()),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
        completion_waiter=reject_completion,
    ).run()
    rendered = render_live_report(report)

    assert report.failure_stage == "RECEIVER_REJECTED"
    assert "PRIVATE RECEIVER DETAIL" not in rendered
    assert "PRIVATE_RECEIVER_STAGE" not in rendered


def test_runner_reports_success_with_no_failure_stage(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    report = MoodleLiveBootstrapRunner(
        config=CONFIG,
        settings=SETTINGS,
        paths=paths,
        auth_host=WebView2EmbeddedAuthHost(CompletingRuntime(paths)),
        site_info_transport_factory=lambda authorization: FakeSiteInfoTransport(authorization),
        profile_root=tmp_path / "profiles",
        completion_timeout_seconds=1,
    ).run()

    assert report.failure_stage == "NO_FAILURE"


def test_configured_runner_reports_prerequisite_failure_without_exception_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aaos.moodle.live_runner as live_runner

    def unavailable_configuration() -> object:
        raise ReceiverError("PRIVATE CONFIGURATION DETAIL")

    monkeypatch.setattr(live_runner, "load_runtime_moodle_configuration", unavailable_configuration)

    assert live_runner.run_configured_moodle_bootstrap() == 1

    rendered = capsys.readouterr().out
    assert "FAILURE_STAGE=PREREQUISITE" in rendered
    assert "PRIVATE CONFIGURATION DETAIL" not in rendered
    assert "EMBEDDED_SSO=NOT_ATTEMPTED" in rendered
    assert "SITE_INFO=NOT_ATTEMPTED" in rendered
