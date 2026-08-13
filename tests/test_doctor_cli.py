from __future__ import annotations

import importlib
import sys
from collections.abc import Awaitable, Callable
from typing import cast

import httpx2
import pytest

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    DoctorReport,
)


def make_report(status: DiagnosticStatus) -> DoctorReport:
    code = DiagnosticCode.CONNECTION_FAILED if status is not DiagnosticStatus.PASS else None
    return DoctorReport(
        (
            DiagnosticResult(
                "python_runtime",
                status,
                "Python runtime checked",
                facts=(DiagnosticFact("python_version", "3.11"),),
                code=code,
            ),
        )
    )


def test_render_report_is_deterministic_and_preserves_result_and_fact_order() -> None:
    from aaos.doctor.rendering import render_report

    report = DoctorReport(
        (
            DiagnosticResult(
                "platform",
                DiagnosticStatus.PASS,
                "Platform information available",
                facts=(
                    DiagnosticFact("os_name", "Windows"),
                    DiagnosticFact("architecture", "AMD64"),
                ),
            ),
            DiagnosticResult(
                "uv",
                DiagnosticStatus.WARN,
                "uv version unavailable",
                code=DiagnosticCode.MISSING_COMMAND,
            ),
        )
    )

    assert render_report(report) == (
        "AAOS doctor: WARN\n"
        "[PASS] platform: Platform information available (os_name=Windows, architecture=AMD64)\n"
        "[WARN] uv: uv version unavailable"
    )


def test_render_report_exposes_only_allowlisted_typed_report_values() -> None:
    from aaos.doctor.rendering import render_report

    sentinels = (
        "raw stdout sentinel",
        "raw stderr sentinel",
        "exception sentinel",
        "prompt sentinel",
        "response body sentinel",
        "token sentinel",
        "environment value sentinel",
    )
    report = DoctorReport(
        (
            DiagnosticResult(
                "uv",
                DiagnosticStatus.PASS,
                "uv version available",
                facts=(
                    DiagnosticFact("uv_version", "0.12.3"),
                    DiagnosticFact("unapproved_fact", "environment value sentinel"),
                ),
            ),
        )
    )

    rendered = render_report(report)

    assert "[PASS]" in rendered
    assert "uv version available" in rendered
    assert "uv_version=0.12.3" in rendered
    assert all(sentinel not in rendered for sentinel in sentinels)


@pytest.mark.parametrize(
    ("status", "expected_exit_code"),
    [
        (DiagnosticStatus.PASS, 0),
        (DiagnosticStatus.WARN, 0),
        (DiagnosticStatus.FAIL, 1),
    ],
)
def test_run_cli_prints_one_report_and_returns_its_exit_code(
    capsys: pytest.CaptureFixture[str], status: DiagnosticStatus, expected_exit_code: int
) -> None:
    from aaos.cli import run_cli

    calls = 0

    def run_doctor() -> DoctorReport:
        nonlocal calls
        calls += 1
        return make_report(status)

    assert run_cli(["doctor"], run_doctor) == expected_exit_code
    captured = capsys.readouterr()
    assert calls == 1
    assert (
        captured.out
        == f"AAOS doctor: {status}\n[{status}] python_runtime: Python runtime checked (python_version=3.11)\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize("argv", [[], ["unknown"], ["doctor", "extra"]])
def test_run_cli_rejects_invalid_arguments_without_running_doctor(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    from aaos.cli import run_cli

    def should_not_run() -> DoctorReport:
        raise AssertionError("invalid CLI input must not run diagnostics")

    assert run_cli(argv, should_not_run) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "usage: aaos doctor\n"


def test_cli_import_does_not_construct_transport_or_run_inspectors() -> None:
    module = importlib.import_module("aaos.cli")

    assert module is not None


def test_main_composes_one_transport_gateway_and_service_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aaos import cli

    created_transports: list[object] = []
    created_gateways: list[FakeGateway] = []
    services: list[FakeDoctorService] = []

    class FakeTransport:
        def __init__(self) -> None:
            created_transports.append(self)

    class FakeGateway:
        def __init__(self, settings: object, transport: object) -> None:
            self.settings = settings
            self.transport = transport
            self.closed = False
            created_gateways.append(self)

        async def aclose(self) -> None:
            self.closed = True

    class FakeDoctorService:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            services.append(self)

        async def run(self) -> DoctorReport:
            closer = cast(Callable[[], Awaitable[None]], self.kwargs["close_model_gateway"])
            await closer()
            return make_report(DiagnosticStatus.PASS)

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(cli, "LMStudioGateway", FakeGateway)
    monkeypatch.setattr(cli, "DoctorService", FakeDoctorService)
    monkeypatch.setattr(sys, "argv", ["aaos", "doctor"])

    assert cli.main() == 0
    assert capsys.readouterr().err == ""
    assert len(created_transports) == 1
    assert len(created_gateways) == 1
    assert len(services) == 1
    assert created_gateways[0].transport is created_transports[0]
    assert services[0].kwargs["model_gateway"] is created_gateways[0]
    assert created_gateways[0].closed is True
