from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from aaos.doctor import commands
from aaos.doctor.commands import (
    COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_BYTES,
    CommandInspector,
    FixedCommandInspector,
    parse_uv_version,
)
from aaos.doctor.models import CommandOutput, DiagnosticCode, UvObservation

RunCommand = Callable[..., subprocess.CompletedProcess[bytes]]


def inspector_for(
    runner: RunCommand,
    *,
    finder: Callable[[str], str | None] | None = None,
) -> FixedCommandInspector:
    return FixedCommandInspector(
        executable_finder=finder or (lambda name: rf"C:\\Tools\\{name}.exe"),
        process_runner=runner,
    )


def test_canonical_uv_spec_executes_and_parses() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"uv 0.12.3 (abc)\n")

    observation = inspector_for(runner).inspect_uv()

    assert observation == UvObservation(version="0.12.3")
    assert calls[0][0] == [r"C:\Tools\uv.exe", "--version"]


def test_canonical_nvidia_spec_executes_with_exact_fixed_arguments() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"safe csv")

    output = inspector_for(runner).inspect_nvidia_output()

    assert output == CommandOutput(text="safe csv")
    assert calls[0][0] == [
        r"C:\Tools\nvidia-smi.exe",
        "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ]
    assert commands.UV_ARGS == ("--version",)
    assert commands.NVIDIA_ARGS == (
        "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    )


def test_both_canonical_invocations_disable_stdin() -> None:
    stdin_values: list[object] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        stdin_values.append(kwargs["stdin"])
        return subprocess.CompletedProcess(args, 0, stdout=b"uv 0.12.3")

    inspector = inspector_for(runner)

    inspector.inspect_uv()
    inspector.inspect_nvidia_output()

    assert stdin_values == [subprocess.DEVNULL, subprocess.DEVNULL]


def test_value_equal_clone_is_rejected_before_discovery_or_execution() -> None:
    discovered: list[str] = []
    executed: list[list[str]] = []

    def finder(name: str) -> str:
        discovered.append(name)
        return rf"C:\Tools\{name}.exe"

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        executed.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"safe")

    inspector = inspector_for(runner, finder=finder)
    clone = commands._CommandSpec(commands._UV_SPEC.executable_name, commands._UV_SPEC.arguments)

    assert inspector._run(clone) == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert discovered == []
    assert executed == []


def test_invocation_uses_the_safe_fixed_run_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(
        "aaos.doctor.commands.os.environ",
        {
            "PATH": r"C:\Tools",
            "PATHEXT": ".EXE;.BAT",
            "SYSTEMROOT": r"C:\Windows",
            "WINDIR": r"C:\Windows",
            "SECRET_TOKEN": "must-not-cross-boundary",
        },
    )

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"uv 0.12.3")

    assert inspector_for(runner).inspect_uv() == UvObservation(version="0.12.3")
    assert calls == [
        (
            [r"C:\Tools\uv.exe", "--version"],
            {
                "shell": False,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "timeout": 5.0,
                "check": False,
                "env": {
                    "PATH": r"C:\Tools",
                    "PATHEXT": ".EXE;.BAT",
                    "SYSTEMROOT": r"C:\Windows",
                    "WINDIR": r"C:\Windows",
                },
            },
        )
    ]


@pytest.mark.parametrize("size", [16_383, 16_384])
def test_output_at_or_below_accepted_bound_is_retained(size: int) -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=b"x" * size)

    output = inspector_for(runner).inspect_nvidia_output()

    assert output == CommandOutput(text="x" * size)


def test_output_above_accepted_bound_is_discarded_without_partial_payload() -> None:
    sentinel = "SENTINEL partial payload"

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=(sentinel.encode() + b"x" * 16_385))

    output = inspector_for(runner).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.OVERSIZED_OUTPUT)
    assert sentinel not in repr(output)


def test_oversized_output_is_rejected_before_invalid_utf8_is_decoded() -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=(b"x" * 16_384) + b"\xff")

    output = inspector_for(runner).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.OVERSIZED_OUTPUT)


def test_missing_discovery_and_file_not_found_map_to_missing_command() -> None:
    def unavailable(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("SENTINEL native path")

    assert inspector_for(unavailable, finder=lambda name: None).inspect_uv() == UvObservation(
        version=None, code=DiagnosticCode.MISSING_COMMAND
    )
    output = inspector_for(unavailable).inspect_uv()
    assert output == UvObservation(version=None, code=DiagnosticCode.MISSING_COMMAND)
    assert "SENTINEL" not in repr(output)


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (FileNotFoundError("SENTINEL missing discovery path"), DiagnosticCode.MISSING_COMMAND),
        (PermissionError("SENTINEL denied discovery path"), DiagnosticCode.PERMISSION_DENIED),
        (OSError("SENTINEL native discovery detail"), DiagnosticCode.COMMAND_FAILED),
    ],
)
def test_discovery_exceptions_map_safely_before_execution(
    error: OSError,
    expected_code: DiagnosticCode,
) -> None:
    runner_called = False

    def finder(name: str) -> str:
        raise error

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        nonlocal runner_called
        runner_called = True
        return subprocess.CompletedProcess(args, 0, stdout=b"must not execute")

    output = inspector_for(runner, finder=finder).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=expected_code)
    assert "SENTINEL" not in repr(output)
    assert not runner_called


def test_permission_denied_maps_without_native_detail() -> None:
    def denied(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise PermissionError("SENTINEL denied")

    output = inspector_for(denied).inspect_uv()

    assert output == UvObservation(version=None, code=DiagnosticCode.PERMISSION_DENIED)
    assert "SENTINEL" not in repr(output)


def test_residual_start_oserror_maps_without_native_path_or_environment_detail() -> None:
    def broken(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise OSError("SENTINEL C:\\secret PATH=do-not-expose")

    output = inspector_for(broken).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert "SENTINEL" not in repr(output)


def test_timeout_maps_without_captured_process_details() -> None:
    def timed_out(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(
            ["SENTINEL command"],
            COMMAND_TIMEOUT_SECONDS,
            output=b"SENTINEL stdout",
            stderr=b"SENTINEL stderr",
        )

    output = inspector_for(timed_out).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.TIMEOUT)
    assert "SENTINEL" not in repr(output)


def test_nonzero_exit_maps_to_command_failed_without_stdout() -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 9, stdout=b"SENTINEL untrusted output")

    output = inspector_for(runner).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert "SENTINEL" not in repr(output)


def test_invalid_utf8_maps_to_malformed_only_after_size_validation() -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=(b"x" * 16_383) + b"\xff")

    assert inspector_for(runner).inspect_nvidia_output() == CommandOutput(
        text=None, code=DiagnosticCode.MALFORMED_OUTPUT
    )


def test_malformed_uv_text_maps_to_malformed_output() -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=b"not uv")

    assert inspector_for(runner).inspect_uv() == UvObservation(
        version=None, code=DiagnosticCode.MALFORMED_OUTPUT
    )


def test_public_surface_is_limited_to_two_diagnostics() -> None:
    inspector = inspector_for(
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=b"safe")
    )

    assert isinstance(inspector, CommandInspector)
    assert {name for name in dir(inspector) if not name.startswith("_")} == {
        "inspect_nvidia_output",
        "inspect_uv",
    }
    assert COMMAND_TIMEOUT_SECONDS == 5.0
    assert MAX_COMMAND_OUTPUT_BYTES == 16_384


def test_parse_uv_version_propagates_safe_failure() -> None:
    assert parse_uv_version(
        CommandOutput(text=None, code=DiagnosticCode.PERMISSION_DENIED)
    ) == UvObservation(version=None, code=DiagnosticCode.PERMISSION_DENIED)


def test_relative_discovery_path_is_normalized() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"uv 0.12.3")

    assert inspector_for(runner, finder=lambda name: r"tools\uv.exe").inspect_uv() == UvObservation(
        version="0.12.3"
    )
    assert calls[0][0] == os.path.abspath(r"tools\uv.exe")
