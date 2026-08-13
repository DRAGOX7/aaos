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
    """Expose the configured doctor command through the project entry point."""
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
