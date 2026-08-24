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

    disallowed_facts = (
        DiagnosticFact("raw_stdout", "raw stdout sentinel"),
        DiagnosticFact("raw_stderr", "raw stderr sentinel"),
        DiagnosticFact("exception_detail", "exception sentinel"),
        DiagnosticFact("model_prompt", "prompt sentinel"),
        DiagnosticFact("response_body", "response body sentinel"),
        DiagnosticFact("access_token", "token sentinel"),
        DiagnosticFact("environment_value", "environment value sentinel"),
    )
    report = DoctorReport(
        (
            DiagnosticResult(
                "uv",
                DiagnosticStatus.PASS,
                "uv version available",
                facts=(
                    DiagnosticFact("uv_version", "0.12.3"),
                    DiagnosticFact("python_version", "3.11"),
                    *disallowed_facts,
                ),
            ),
        )
    )

    rendered = render_report(report)
    sentinel_values = tuple(str(fact.value) for fact in disallowed_facts)

    assert "[PASS]" in rendered
    assert "uv version available" in rendered
    assert "uv_version=0.12.3" in rendered
    assert "python_version=3.11" in rendered
    assert all(sentinel not in rendered for sentinel in sentinel_values)


def test_render_report_escapes_untrusted_fact_controls_without_forging_lines() -> None:
    from aaos.doctor.rendering import render_report

    report = DoctorReport(
        (
            DiagnosticResult(
                "platform",
                DiagnosticStatus.PASS,
                "Platform information available",
                facts=(
                    DiagnosticFact("os_name", "Windows\r\n[FAIL] forged"),
                    DiagnosticFact("cpu_model", "CPU\x1b[2J\x00\x7f\x85"),
                    DiagnosticFact(
                        "architecture", "AMD64 \u2603 \u0627\u0644\u0639\u0631\u0628\u064a\u0629"
                    ),
                ),
            ),
        )
    )

    rendered = render_report(report)

    assert rendered.splitlines() == [
        "AAOS doctor: PASS",
        "[PASS] platform: Platform information available "
        "(os_name=Windows\\u000D\\u000A[FAIL] forged, "
        "cpu_model=CPU\\u001B[2J\\u0000\\u007F\\u0085, "
        "architecture=AMD64 \u2603 \u0627\u0644\u0639\u0631\u0628\u064a\u0629)",
    ]
    assert all(character.isprintable() or character == "\n" for character in rendered)
    assert rendered.count("\n") == 1
    assert "\x1b" not in rendered
    assert "\r" not in rendered


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


def test_fresh_cli_import_does_not_construct_transport_gateway_or_collectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.doctor.commands as commands
    import aaos.doctor.nvidia as nvidia
    import aaos.doctor.reference as reference
    import aaos.doctor.runtime as runtime
    import aaos.doctor.service as service
    import aaos.doctor.system as system
    import aaos.inference.lm_studio as lm_studio

    constructions: list[str] = []

    def probe_constructor(label: str) -> type[object]:
        class ConstructorProbe:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                constructions.append(label)

        return ConstructorProbe

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", probe_constructor("transport"))
    monkeypatch.setattr(lm_studio, "LMStudioGateway", probe_constructor("gateway"))
    monkeypatch.setattr(runtime, "LocalRuntimeInspector", probe_constructor("runtime"))
    monkeypatch.setattr(system, "LocalSystemInspector", probe_constructor("system"))
    monkeypatch.setattr(commands, "FixedCommandInspector", probe_constructor("command"))
    monkeypatch.setattr(nvidia, "CommandNvidiaInspector", probe_constructor("nvidia"))
    monkeypatch.setattr(reference, "LocalReferenceInspector", probe_constructor("reference"))
    monkeypatch.setattr(service, "DoctorService", probe_constructor("service"))
    monkeypatch.delitem(sys.modules, "aaos.cli", raising=False)

    module = importlib.import_module("aaos.cli")

    assert module is not None
    assert constructions == []


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


def test_main_dispatches_the_narrow_moodle_bootstrap_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aaos import cli

    calls = 0

    def run_bootstrap() -> int:
        nonlocal calls
        calls += 1
        print("SAFE BOOTSTRAP OUTPUT")
        return 0

    monkeypatch.setattr(cli, "_run_configured_moodle_bootstrap", run_bootstrap, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "bootstrap"])

    assert cli.main() == 0
    assert calls == 1
    assert capsys.readouterr().out == "SAFE BOOTSTRAP OUTPUT\n"


def test_main_dispatches_the_explicit_system_browser_moodle_bootstrap_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import cli

    captured: dict[str, str] = {}

    def run_bootstrap(*, auth_host: str) -> int:
        captured["auth_host"] = auth_host
        return 0

    monkeypatch.setattr(cli, "_run_configured_moodle_bootstrap", run_bootstrap, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["aaos", "moodle", "bootstrap", "--auth-host", "system"],
    )

    assert cli.main() == 0
    assert captured == {"auth_host": "system"}


def test_main_dispatches_the_explicit_confirmed_return_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import cli

    calls = 0

    def run_diagnostic() -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(
        cli,
        "_run_configured_moodle_confirmed_return_diagnostic",
        run_diagnostic,
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-confirmed-return"])

    assert cli.main() == 0
    assert calls == 1


def test_main_dispatches_the_narrow_browser_auth_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    calls = 0

    def provision() -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(cli, "_run_configured_moodle_browser_auth", provision, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "browser-auth"])

    assert cli.main() == 0
    assert calls == 1


def test_main_dispatches_session_lifetime_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(cli, "_run_moodle_session_lifetime_diagnostic", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-session-lifetime"])
    assert cli.main() == 0


def test_main_dispatches_course_index_structure_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(
        cli, "_run_configured_course_index_structure_diagnostic", lambda: 0, raising=False
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-course-index-structure"])
    assert cli.main() == 0


def test_main_dispatches_course_index_loading_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(
        cli, "_run_configured_course_index_loading_diagnostic", lambda: 0, raising=False
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-course-index-loading"])
    assert cli.main() == 0


def test_main_dispatches_static_asset_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(cli, "_run_configured_static_asset_diagnostic", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-static-assets"])
    assert cli.main() == 0


def test_main_dispatches_bootstrap_route_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(
        cli, "_run_configured_moodle_bootstrap_route_diagnostic", lambda: 0, raising=False
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-bootstrap-routes"])

    assert cli.main() == 0


def test_main_dispatches_moodle_handler_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    monkeypatch.setattr(cli, "_run_configured_moodle_handler_diagnostic", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "diagnose-moodle-handlers"])

    assert cli.main() == 0
