"""Minimal command-line entry point for explicitly invoked AAOS diagnostics."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Sequence

import httpx2

from aaos.config import DEFAULT_SETTINGS
from aaos.doctor.commands import FixedCommandInspector
from aaos.doctor.models import DoctorReport
from aaos.doctor.nvidia import CommandNvidiaInspector
from aaos.doctor.reference import LocalReferenceInspector
from aaos.doctor.rendering import render_report
from aaos.doctor.runtime import LocalRuntimeInspector
from aaos.doctor.service import DoctorService
from aaos.doctor.system import LocalSystemInspector
from aaos.inference.lm_studio import LMStudioGateway


def run_cli(argv: Sequence[str], run_doctor: Callable[[], DoctorReport]) -> int:
    """Run the only supported command with exact, safe usage behavior."""
    if list(argv) != ["doctor"]:
        print("usage: aaos doctor", file=sys.stderr)
        return 2
    report = run_doctor()
    print(render_report(report))
    return report.exit_code


def main() -> int:
    """Expose the configured AAOS command through the project entry point."""
    if sys.argv[1:] == ["moodle", "bootstrap"]:
        return _run_configured_moodle_bootstrap()
    if sys.argv[1:] == ["moodle", "bootstrap", "--auth-host", "system"]:
        return _run_configured_moodle_bootstrap(auth_host="system")
    if sys.argv[1:] == ["moodle", "diagnose-confirmed-return"]:
        return _run_configured_moodle_confirmed_return_diagnostic()
    if sys.argv[1:] == ["moodle", "browser-auth"]:
        return _run_configured_moodle_browser_auth()
    if sys.argv[1:] == ["moodle", "browser-smoke-courses"]:
        return _run_configured_moodle_browser_smoke_courses()
    return run_cli(sys.argv[1:], _run_configured_doctor)


def _run_configured_doctor() -> DoctorReport:
    return asyncio.run(_run_configured_doctor_async())


async def _run_configured_doctor_async() -> DoctorReport:
    command_inspector = FixedCommandInspector()
    gateway = LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())
    service = DoctorService(
        settings=DEFAULT_SETTINGS,
        runtime_inspector=LocalRuntimeInspector(),
        system_inspector=LocalSystemInspector(),
        command_inspector=command_inspector,
        nvidia_inspector=CommandNvidiaInspector(command_inspector),
        reference_inspector=LocalReferenceInspector(),
        model_gateway=gateway,
        close_model_gateway=gateway.aclose,
    )
    return await service.run()


def _run_configured_moodle_bootstrap(*, auth_host: str = "webview2") -> int:
    """Run the one human-controlled Moodle bootstrap and site-info gate."""
    from aaos.moodle.live_runner import run_configured_moodle_bootstrap

    if auth_host == "system":
        return run_configured_moodle_bootstrap(auth_host="system")
    if auth_host == "webview2":
        return run_configured_moodle_bootstrap(auth_host="webview2")
    return 2


def _run_configured_moodle_confirmed_return_diagnostic() -> int:
    """Run the separate, human-controlled confirmation-page diagnostic."""
    from aaos.moodle.live_runner import run_configured_moodle_confirmed_return_diagnostic

    return run_configured_moodle_confirmed_return_diagnostic()


def _run_configured_moodle_browser_auth() -> int:
    """Reserve an explicit browser-only command; capability loading stays human-gated."""
    from aaos.moodle.browser_auth import run_configured_browser_auth

    return run_configured_browser_auth()


def _run_configured_moodle_browser_smoke_courses() -> int:
    """Run only the explicit, memory-only browser course-discovery smoke."""
    from aaos.moodle.browser import run_configured_browser_course_smoke

    return run_configured_browser_course_smoke()
