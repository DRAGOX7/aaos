"""The single human-controlled Phase 2 Moodle bootstrap composition command."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx2

from aaos.moodle.api import (
    MoodleApiError,
    MoodleApiTransport,
    SingleUseSiteInfoProbeAuthorization,
)
from aaos.moodle.api_parsing import SafeSiteInfo
from aaos.moodle.auth import FileMoodleCredentialReader
from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.embedded_auth import WebView2EmbeddedAuthHost, WebView2RuntimeUnavailable
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.native_webview2 import NativeWebView2Runtime
from aaos.moodle.receiver import (
    BootstrapCompletion,
    PendingBootstrapAttempt,
    ReceiverError,
    begin_bootstrap_attempt,
    cancel_bootstrap_attempt,
    load_runtime_moodle_configuration,
    wait_for_bootstrap_completion,
)

_Status = Literal["NOT_ATTEMPTED", "PASS", "FAIL"]
FailureStage = Literal[
    "NO_FAILURE",
    "PREREQUISITE",
    "ATTEMPT_CREATION",
    "WEBVIEW2_LAUNCH",
    "WEBVIEW2_HOST_ERROR",
    "WEBVIEW2_TIMEOUT",
    "WEBVIEW2_CLEANUP",
    "CALLBACK_NOT_RECEIVED",
    "RECEIVER_REJECTED",
    "CREDENTIAL_NOT_PERSISTED",
    "SITE_INFO_AUTHORIZATION",
    "SITE_INFO_REQUEST",
    "SITE_INFO_PARSE",
]
_RELEVANT_FUNCTION_PREFIXES = (
    "core_course_",
    "mod_assign_",
    "mod_forum_",
    "core_files_",
    "mod_resource_",
)
_RELEVANT_FUNCTIONS = frozenset({"core_webservice_get_site_info"})


class SiteInfoTransport(Protocol):
    async def get_site_info(self) -> SafeSiteInfo:
        """Perform the one authorized site-info request."""

    async def aclose(self) -> None:
        """Close the owned HTTP transport."""


SiteInfoTransportFactory = Callable[[SingleUseSiteInfoProbeAuthorization], SiteInfoTransport]


class CompletionWaiter(Protocol):
    def __call__(
        self,
        paths: AuthPaths,
        attempt: PendingBootstrapAttempt,
        *,
        timeout_seconds: int,
    ) -> BootstrapCompletion:
        """Return non-secret proof for the same one-time bootstrap attempt."""


@dataclass(frozen=True, slots=True)
class LiveBootstrapReport:
    """Only the allowlisted public outcome of the bounded live operation."""

    failure_stage: FailureStage
    embedded_sso: _Status
    callback_received: _Status
    token_persisted: _Status
    webview2_cleanup: _Status
    site_info: _Status
    canonical_wwwroot: str | None = None
    release: str | None = None
    relevant_available_functions: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        return (
            0
            if all(
                value == "PASS"
                for value in (
                    self.embedded_sso,
                    self.callback_received,
                    self.token_persisted,
                    self.webview2_cleanup,
                    self.site_info,
                )
            )
            else 1
        )


def _relevant_functions(functions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        function
        for function in functions
        if function in _RELEVANT_FUNCTIONS or function.startswith(_RELEVANT_FUNCTION_PREFIXES)
    )


def _receiver_failure_stage(value: object) -> FailureStage:
    if value == "CALLBACK_NOT_RECEIVED":
        return "CALLBACK_NOT_RECEIVED"
    if value == "RECEIVER_REJECTED":
        return "RECEIVER_REJECTED"
    return "RECEIVER_REJECTED"


def _site_info_failure_stage(value: object) -> FailureStage:
    if value == "CREDENTIAL_NOT_PERSISTED":
        return "CREDENTIAL_NOT_PERSISTED"
    if value == "SITE_INFO_AUTHORIZATION":
        return "SITE_INFO_AUTHORIZATION"
    if value == "SITE_INFO_PARSE":
        return "SITE_INFO_PARSE"
    return "SITE_INFO_REQUEST"


def render_live_report(report: LiveBootstrapReport) -> str:
    """Render only the fixed status surface and safe parsed site-info fields."""
    lines = [
        f"FAILURE_STAGE={report.failure_stage}",
        f"EMBEDDED_SSO={report.embedded_sso}",
        f"CALLBACK_RECEIVED={report.callback_received}",
        f"TOKEN_PERSISTED={report.token_persisted}",
        f"WEBVIEW2_CLEANUP={report.webview2_cleanup}",
        f"SITE_INFO={report.site_info}",
        f"CANONICAL_WWWROOT={report.canonical_wwwroot or 'unavailable'}",
        f"MOODLE_RELEASE={report.release or 'unavailable'}",
        "RELEVANT_AVAILABLE_FUNCTIONS:",
        *report.relevant_available_functions,
        "NOTES:",
        "SITE_INFO_ONLY",
        "FUNCTION_AVAILABILITY_DOES_NOT_AUTHORIZE_ENTITY_CALLS",
        "NO_ENTITY_API_OR_DATABASE_SYNC",
    ]
    return "\n".join(lines)


class MoodleLiveBootstrapRunner:
    """Compose exactly one receiver-bound login and one site-info operation."""

    def __init__(
        self,
        *,
        config: MoodleBootstrapConfig,
        settings: MoodleSettings,
        paths: AuthPaths,
        auth_host: WebView2EmbeddedAuthHost,
        site_info_transport_factory: SiteInfoTransportFactory,
        profile_root: Path,
        completion_timeout_seconds: int,
        completion_waiter: CompletionWaiter = wait_for_bootstrap_completion,
    ) -> None:
        self._config = config
        self._settings = settings
        self._paths = paths
        self._auth_host = auth_host
        self._site_info_transport_factory = site_info_transport_factory
        self._profile_root = profile_root
        self._completion_timeout_seconds = completion_timeout_seconds
        self._completion_waiter = completion_waiter

    def run(self) -> LiveBootstrapReport:
        try:
            attempt = begin_bootstrap_attempt(self._config, self._paths)
        except ReceiverError:
            return _failed_report(failure_stage="ATTEMPT_CREATION")
        try:
            try:
                result = self._auth_host.start(
                    self._config.canonical_wwwroot + "/admin/tool/mobile/launch.php",
                    self._profile_root,
                    attempt.passport,
                    self._config.timeout_seconds,
                )
            except (OSError, ValueError, WebView2RuntimeUnavailable):
                return _failed_report(failure_stage="WEBVIEW2_LAUNCH", embedded_sso="FAIL")
            if result != "CALLBACK_DISPATCHED":
                return self._auth_failure_report(result)
            try:
                self._completion_waiter(
                    self._paths,
                    attempt,
                    timeout_seconds=self._completion_timeout_seconds,
                )
            except ReceiverError as error:
                return _failed_report(
                    failure_stage=_receiver_failure_stage(error.failure_stage),
                    embedded_sso="PASS",
                    callback_received="FAIL",
                    webview2_cleanup="PASS",
                )
            return asyncio.run(self._get_site_info())
        finally:
            try:
                cancel_bootstrap_attempt(self._paths, attempt.attempt_id)
            except ReceiverError:
                pass

    def _auth_failure_report(self, result: str) -> LiveBootstrapReport:
        if result == "USER_CANCELLED":
            failure_stage: FailureStage = "CALLBACK_NOT_RECEIVED"
        else:
            failure_stage = self._auth_host.last_failure_stage or "WEBVIEW2_HOST_ERROR"
        embedded_sso: _Status = (
            "PASS" if self._auth_host.last_interaction_result == "CALLBACK_DISPATCHED" else "FAIL"
        )
        webview2_cleanup: _Status = (
            "PASS"
            if self._auth_host.last_cleanup_pass is True
            else "FAIL"
            if self._auth_host.last_cleanup_pass is False
            else "NOT_ATTEMPTED"
        )
        return _failed_report(
            failure_stage=failure_stage,
            embedded_sso=embedded_sso,
            webview2_cleanup=webview2_cleanup,
        )

    async def _get_site_info(self) -> LiveBootstrapReport:
        authorization = SingleUseSiteInfoProbeAuthorization()
        transport: SiteInfoTransport | None = None
        try:
            transport = self._site_info_transport_factory(authorization)
            site_info = await transport.get_site_info()
        except MoodleApiError as error:
            return _failed_report(
                failure_stage=_site_info_failure_stage(error.failure_stage),
                embedded_sso="PASS",
                callback_received="PASS",
                token_persisted="PASS",
                webview2_cleanup="PASS",
                site_info="FAIL",
            )
        finally:
            if transport is not None:
                await transport.aclose()
        return LiveBootstrapReport(
            failure_stage="NO_FAILURE",
            embedded_sso="PASS",
            callback_received="PASS",
            token_persisted="PASS",
            webview2_cleanup="PASS",
            site_info="PASS",
            canonical_wwwroot=site_info.canonical_wwwroot,
            release=site_info.release,
            relevant_available_functions=_relevant_functions(site_info.available_functions),
        )


def _failed_report(
    *,
    failure_stage: FailureStage,
    embedded_sso: _Status = "NOT_ATTEMPTED",
    callback_received: _Status = "NOT_ATTEMPTED",
    token_persisted: _Status = "NOT_ATTEMPTED",
    webview2_cleanup: _Status = "NOT_ATTEMPTED",
    site_info: _Status = "NOT_ATTEMPTED",
) -> LiveBootstrapReport:
    return LiveBootstrapReport(
        failure_stage=failure_stage,
        embedded_sso=embedded_sso,
        callback_received=callback_received,
        token_persisted=token_persisted,
        webview2_cleanup=webview2_cleanup,
        site_info=site_info,
    )


def _default_site_info_transport_factory(
    settings: MoodleSettings, canonical_wwwroot: str, paths: AuthPaths
) -> SiteInfoTransportFactory:
    def factory(authorization: SingleUseSiteInfoProbeAuthorization) -> MoodleApiTransport:
        return MoodleApiTransport(
            settings=settings,
            canonical_wwwroot=canonical_wwwroot,
            transport=httpx2.AsyncHTTPTransport(),
            credential_reader=FileMoodleCredentialReader(paths),
            authorization=authorization,
        )

    return factory


def run_configured_moodle_bootstrap() -> int:
    """Run the manually invoked Phase 2 gate; imports alone do nothing."""
    try:
        config, settings, paths = load_runtime_moodle_configuration()
        helper_path = (
            Path(__file__).resolve().parents[3]
            / "native"
            / "AAOS.WebView2Host"
            / "bin"
            / "Release"
            / "net8.0-windows"
            / "AAOS.WebView2Host.exe"
        )
        runner = MoodleLiveBootstrapRunner(
            config=config,
            settings=settings,
            paths=paths,
            auth_host=WebView2EmbeddedAuthHost(NativeWebView2Runtime(helper_path)),
            site_info_transport_factory=_default_site_info_transport_factory(
                settings, config.canonical_wwwroot, paths
            ),
            profile_root=paths.browser_state_dir / "embedded_profiles",
            completion_timeout_seconds=config.timeout_seconds,
        )
        report = runner.run()
    except (ReceiverError, ValueError, OSError):
        report = _failed_report(failure_stage="PREREQUISITE")
    print(render_live_report(report))
    return report.exit_code
