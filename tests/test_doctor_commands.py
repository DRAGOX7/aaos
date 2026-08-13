from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from aaos.doctor.commands import (
    COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_BYTES,
    CommandInspector,
    FixedCommandInspector,
    parse_uv_version,
)
from aaos.doctor.models import CommandOutput, DiagnosticCode, UvObservation

ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def completed_process(
    *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=["fixed-command"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_uv_success_uses_fixed_process_boundary_and_returns_parsed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    discovered: list[str] = []

    def find_executable(name: str) -> str | None:
        discovered.append(name)
        return r"C:\Tools\uv.exe"

    def run_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kwargs))
        return completed_process(stdout=b"uv 0.12.3 (a1b2c3 2026-08-01)\n")

    monkeypatch.setattr(
        "aaos.doctor.commands.os.environ",
        {
            "PATH": r"C:\Tools",
            "PATHEXT": ".EXE;.BAT",
            "SYSTEMROOT": r"C:\Windows",
            "WINDIR": r"C:\Windows",
            "SECRET_TOKEN": "must-not-cross-boundary",
            "UV_INDEX_URL": "must-not-cross-boundary",
        },
    )

    observation = FixedCommandInspector(find_executable, run_process).inspect_uv()

    assert observation == UvObservation(version="0.12.3")
    assert discovered == ["uv"]
    assert calls == [
        (
            [r"C:\Tools\uv.exe", "--version"],
            {
                "shell": False,
                "capture_output": True,
                "text": False,
                "check": False,
                "timeout": 5.0,
                "env": {
                    "PATH": r"C:\Tools",
                    "PATHEXT": ".EXE;.BAT",
                    "SYSTEMROOT": r"C:\Windows",
                    "WINDIR": r"C:\Windows",
                },
            },
        )
    ]
    assert COMMAND_TIMEOUT_SECONDS == 5.0


def test_nvidia_inspection_uses_only_the_approved_fixed_query() -> None:
    calls: list[list[str]] = []

    def find_executable(name: str) -> str | None:
        assert name == "nvidia-smi"
        return r"C:\Windows\System32\nvidia-smi.exe"

    def run_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return completed_process(stdout=b"safe csv")

    output = FixedCommandInspector(find_executable, run_process).inspect_nvidia_output()

    assert output == CommandOutput(text="safe csv")
    assert calls == [
        [
            r"C:\Windows\System32\nvidia-smi.exe",
            "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
    ]


def test_relative_discovery_result_is_normalized_before_process_execution() -> None:
    calls: list[list[str]] = []

    def run_process(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return completed_process(stdout=b"uv 0.12.3")

    observation = FixedCommandInspector(lambda name: r"tools\uv.exe", run_process).inspect_uv()

    assert observation == UvObservation(version="0.12.3")
    assert calls == [[os.path.abspath(r"tools\uv.exe"), "--version"]]


def test_missing_executable_returns_typed_failure_without_running_process() -> None:
    def fail_if_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        pytest.fail("process runner must not be called when discovery fails")

    output = FixedCommandInspector(lambda name: None, fail_if_run).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)


@pytest.mark.parametrize(
    ("native_error", "expected_code"),
    [
        (FileNotFoundError("SENTINEL native path"), DiagnosticCode.MISSING_COMMAND),
        (PermissionError("SENTINEL native access detail"), DiagnosticCode.PERMISSION_DENIED),
        (
            subprocess.TimeoutExpired(["SENTINEL", "native", "command"], timeout=5.0),
            DiagnosticCode.TIMEOUT,
        ),
    ],
)
def test_expected_native_failures_return_only_safe_codes(
    native_error: Exception,
    expected_code: DiagnosticCode,
) -> None:
    def raise_native_error(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise native_error

    output = FixedCommandInspector(
        lambda name: r"C:\Tools\uv.exe", raise_native_error
    ).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=expected_code)
    assert "SENTINEL" not in repr(output)


def test_timeout_is_enforced_by_the_process_runner() -> None:
    observed_timeout: float | None = None

    def time_out(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        nonlocal observed_timeout
        observed_timeout = kwargs["timeout"]
        raise subprocess.TimeoutExpired(args, timeout=kwargs["timeout"])

    output = FixedCommandInspector(lambda name: r"C:\Tools\uv.exe", time_out).inspect_uv()

    assert output == UvObservation(version=None, code=DiagnosticCode.TIMEOUT)
    assert observed_timeout == COMMAND_TIMEOUT_SECONDS == 5.0


def test_nonzero_exit_discards_stderr_and_returns_command_failed() -> None:
    sentinel = "SENTINEL private stderr"

    def fail_command(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return completed_process(
            stdout=b"untrusted stdout",
            stderr=sentinel.encode(),
            returncode=9,
        )

    output = FixedCommandInspector(
        lambda name: r"C:\Tools\nvidia-smi.exe", fail_command
    ).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert sentinel not in repr(output)


@pytest.mark.parametrize("raw_output", [b"not uv", b"uv \xff\n"])
def test_malformed_or_non_utf8_uv_output_is_typed(raw_output: bytes) -> None:
    def run_process(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return completed_process(stdout=raw_output)

    observation = FixedCommandInspector(lambda name: r"C:\Tools\uv.exe", run_process).inspect_uv()

    assert observation == UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)


def test_oversized_encoded_output_is_rejected_before_uv_parsing() -> None:
    oversized_malformed_output = b"x" * (MAX_COMMAND_OUTPUT_BYTES + 1)

    def run_process(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return completed_process(stdout=oversized_malformed_output)

    observation = FixedCommandInspector(lambda name: r"C:\Tools\uv.exe", run_process).inspect_uv()

    assert observation == UvObservation(version=None, code=DiagnosticCode.OVERSIZED_OUTPUT)
    assert MAX_COMMAND_OUTPUT_BYTES == 16_384


def test_oversized_stderr_is_rejected_without_retaining_it() -> None:
    sentinel = b"SENTINEL" * (MAX_COMMAND_OUTPUT_BYTES // len(b"SENTINEL") + 1)

    def run_process(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return completed_process(stdout=b"safe", stderr=sentinel)

    output = FixedCommandInspector(
        lambda name: r"C:\Tools\nvidia-smi.exe", run_process
    ).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.OVERSIZED_OUTPUT)
    assert "SENTINEL" not in repr(output)


def test_parse_uv_version_propagates_runner_failure_without_text() -> None:
    observation = parse_uv_version(CommandOutput(text=None, code=DiagnosticCode.PERMISSION_DENIED))

    assert observation == UvObservation(version=None, code=DiagnosticCode.PERMISSION_DENIED)


def test_fixed_inspector_implements_protocol_without_public_arbitrary_runner() -> None:
    inspector = FixedCommandInspector(lambda name: None, subprocess.run)

    assert isinstance(inspector, CommandInspector)
    assert {name for name in dir(inspector) if not name.startswith("_")} == {
        "inspect_nvidia_output",
        "inspect_uv",
    }
