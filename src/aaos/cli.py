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
    if sys.argv[1:3] == ["phase5", "v4"]:
        from aaos.agent.evaluation.v4_cli import run_phase5_v4_command

        return run_phase5_v4_command(sys.argv[3:])
    if sys.argv[1:] == ["sync"]:
        return _run_configured_sync_workflow()
    if sys.argv[1:2] == ["documents"]:
        from aaos.documents.cli import run_documents_command

        return run_documents_command(sys.argv[2:])
    if sys.argv[1:2] == ["rag"]:
        return _run_configured_rag(sys.argv[2:])
    if sys.argv[1:2] == ["notify"]:
        from aaos.notifications.cli import run_notify_command

        return run_notify_command(sys.argv[2:])
    if sys.argv[1:3] == ["model", "chatgpt-plan"]:
        from aaos.inference.chatgpt_plan_cli import run_chatgpt_plan_command

        return run_chatgpt_plan_command(sys.argv[3:])
    if sys.argv[1:2] == ["monitor"]:
        from aaos.notifications.cli import run_monitor_command

        return run_monitor_command(sys.argv[2:])
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
    if sys.argv[1:] == ["moodle", "sync"]:
        return _run_configured_moodle_sync()
    if sys.argv[1:] == ["moodle", "diagnose-session-lifetime"]:
        return _run_moodle_session_lifetime_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-course-index-structure"]:
        return _run_configured_course_index_structure_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-course-index-loading"]:
        return _run_configured_course_index_loading_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-static-assets"]:
        return _run_configured_static_asset_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-bootstrap-routes"]:
        return _run_configured_moodle_bootstrap_route_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-moodle-handlers"]:
        return _run_configured_moodle_handler_diagnostic()
    if sys.argv[1:] == ["moodle", "diagnose-final-bootstrap-script"]:
        return _run_configured_final_bootstrap_script_diagnostic()
    return run_cli(sys.argv[1:], _run_configured_doctor)


def _run_configured_sync_workflow() -> int:
    """Run the explicit end-to-end academic synchronization workflow lazily."""
    from aaos.documents.cli import run_documents_command
    from aaos.sync import run_sync_workflow

    return run_sync_workflow(
        run_moodle_sync=_run_configured_moodle_sync,
        run_document_ingest=lambda: run_documents_command(["ingest-new"]),
        run_rag_index=lambda: _run_configured_rag(["index-new"]),
    )


def _run_configured_rag(argv: Sequence[str]) -> int:
    """Run the explicit local RAG command surface lazily."""
    from aaos.rag.cli import run_rag_command

    return run_rag_command(argv)


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


def _run_configured_moodle_sync() -> int:
    """Run one explicit, complete live Moodle academic synchronization."""
    from aaos.moodle.live_sync import run_configured_moodle_sync

    return run_configured_moodle_sync()


def _run_moodle_session_lifetime_diagnostic() -> int:
    from aaos.moodle.session_lifetime import run_configured_session_lifetime_diagnostic

    return run_configured_session_lifetime_diagnostic()


def _run_configured_course_index_structure_diagnostic() -> int:
    """Run one explicit, content-free COURSE_INDEX structure diagnostic."""
    from aaos.moodle.browser import run_configured_course_index_structure_diagnostic

    return run_configured_course_index_structure_diagnostic()


def _run_configured_course_index_loading_diagnostic() -> int:
    """Run one explicit, privacy-safe COURSE_INDEX loading diagnostic."""
    from aaos.moodle.browser import run_configured_course_index_loading_diagnostic

    return run_configured_course_index_loading_diagnostic()


def _run_configured_static_asset_diagnostic() -> int:
    from aaos.moodle.browser import run_configured_static_asset_diagnostic

    return run_configured_static_asset_diagnostic()


def _run_configured_moodle_bootstrap_route_diagnostic() -> int:
    from aaos.moodle.browser import run_configured_moodle_bootstrap_route_diagnostic

    return run_configured_moodle_bootstrap_route_diagnostic()


def _run_configured_moodle_handler_diagnostic() -> int:
    from aaos.moodle.browser import run_configured_moodle_handler_diagnostic

    return run_configured_moodle_handler_diagnostic()


def _run_configured_final_bootstrap_script_diagnostic() -> int:
    from aaos.moodle.browser import run_configured_final_bootstrap_script_diagnostic

    return run_configured_final_bootstrap_script_diagnostic()
