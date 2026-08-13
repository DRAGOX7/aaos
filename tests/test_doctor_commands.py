from __future__ import annotations

import os
import subprocess
import threading
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


class ControlledPipe:
    def __init__(
        self,
        data: bytes = b"",
        *,
        read_error: OSError | None = None,
        block_until: threading.Event | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._data = data
        self._offset = 0
        self._read_error = read_error
        self._block_until = block_until
        self._events = events if events is not None else []
        self.closed = False
        self.max_requested = 0
        self.reader_finished = threading.Event()

    def read(self, size: int = -1) -> bytes:
        self.max_requested = max(self.max_requested, size)
        try:
            if self._block_until is not None:
                self._block_until.wait()
            if self._read_error is not None:
                raise self._read_error
            if self._offset >= len(self._data):
                return b""
            end = len(self._data) if size < 0 else self._offset + size
            chunk = self._data[self._offset : end]
            self._offset += len(chunk)
            return chunk
        finally:
            if self._offset >= len(self._data) or self._read_error is not None:
                self.reader_finished.set()

    def close(self) -> None:
        self.closed = True
        self._events.append("close_stdout")
        if self._block_until is not None:
            self._block_until.set()


class LifecycleProcess:
    def __init__(
        self,
        stdout: ControlledPipe,
        *,
        returncode: int = 0,
        running: bool = False,
        require_kill: bool = False,
        errors: dict[str, OSError] | None = None,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.running = running
        self.require_kill = require_kill
        self.errors = errors or {}
        self.events: list[str] = stdout._events
        self.reaped = False
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        if error := self.errors.get("poll"):
            raise error
        return None if self.running else self.returncode

    def terminate(self) -> None:
        self.events.append("terminate")
        self.terminated = True
        if error := self.errors.get("terminate"):
            raise error
        if not self.require_kill:
            self.running = False
            if self.stdout._block_until is not None:
                self.stdout._block_until.set()

    def kill(self) -> None:
        self.events.append("kill")
        self.killed = True
        if error := self.errors.get("kill"):
            raise error
        self.running = False
        if self.stdout._block_until is not None:
            self.stdout._block_until.set()

    def wait(self, timeout: float | None = None) -> int:
        self.events.append("wait")
        if error := self.errors.get("wait"):
            self.errors.pop("wait")
            raise error
        if self.running:
            raise subprocess.TimeoutExpired(["redacted"], timeout or 0.0)
        self.reaped = True
        return self.returncode


PopenFactory = Callable[..., LifecycleProcess]


def inspector_for(
    process: LifecycleProcess,
    *,
    finder: Callable[[str], str | None] | None = None,
    clock: Callable[[], float] | None = None,
    calls: list[tuple[list[str], dict[str, Any]]] | None = None,
) -> FixedCommandInspector:
    recorded_calls = calls if calls is not None else []

    def start(args: list[str], **kwargs: Any) -> LifecycleProcess:
        recorded_calls.append((args, kwargs))
        return process

    return FixedCommandInspector(
        finder or (lambda name: rf"C:\Tools\{name}.exe"),
        start,
        clock or (lambda: 0.0),
    )


def assert_clean(process: LifecycleProcess) -> None:
    assert process.reaped
    assert not process.running
    assert process.stdout.closed
    assert process.stdout.reader_finished.is_set()
    assert not any(thread.name == "aaos-doctor-stdout" for thread in threading.enumerate())


def test_uv_success_uses_exact_safe_popen_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    process = LifecycleProcess(ControlledPipe(b"uv 0.12.3 (abc)\n", events=events))
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

    observation = inspector_for(process, calls=calls).inspect_uv()

    assert observation == UvObservation(version="0.12.3")
    assert calls == [
        (
            [r"C:\Tools\uv.exe", "--version"],
            {
                "shell": False,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "text": False,
                "env": {
                    "PATH": r"C:\Tools",
                    "PATHEXT": ".EXE;.BAT",
                    "SYSTEMROOT": r"C:\Windows",
                    "WINDIR": r"C:\Windows",
                },
            },
        )
    ]
    assert_clean(process)


def test_nvidia_uses_exact_fixed_arguments() -> None:
    process = LifecycleProcess(ControlledPipe(b"safe csv"))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    output = inspector_for(process, calls=calls).inspect_nvidia_output()

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
    assert_clean(process)


def test_canonical_specs_are_accepted_but_value_equal_clone_is_rejected() -> None:
    discovered: list[str] = []
    started: list[str] = []
    process = LifecycleProcess(ControlledPipe(b"uv 0.12.3"))

    def find(name: str) -> str:
        discovered.append(name)
        return rf"C:\Tools\{name}.exe"

    def start(*args: object, **kwargs: object) -> LifecycleProcess:
        started.append("start")
        return process

    inspector = FixedCommandInspector(find, start, lambda: 0.0)

    assert inspector._run(commands._UV_SPEC) == CommandOutput(text="uv 0.12.3")
    clone = commands._CommandSpec(commands._UV_SPEC.executable_name, commands._UV_SPEC.arguments)
    assert inspector._run(clone) == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert discovered == ["uv"]
    assert started == ["start"]


@pytest.mark.parametrize("size", [16_383, 16_384])
def test_output_at_or_below_bound_is_retained(size: int) -> None:
    process = LifecycleProcess(ControlledPipe(b"x" * size))

    output = inspector_for(process).inspect_nvidia_output()

    assert output.text == "x" * size
    assert output.code is None
    assert_clean(process)


def test_in_flight_overflow_terminates_reaps_and_retains_no_extra_byte() -> None:
    events: list[str] = []
    pipe = ControlledPipe(b"x" * 16_385, events=events)
    process = LifecycleProcess(pipe, running=True)

    output = inspector_for(process).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.OVERSIZED_OUTPUT)
    assert pipe.max_requested <= MAX_COMMAND_OUTPUT_BYTES + 1
    assert process.terminated
    assert not process.killed
    assert events == ["terminate", "wait", "close_stdout"]
    assert_clean(process)


def test_missing_discovery_and_start_file_not_found_are_safe() -> None:
    process = LifecycleProcess(ControlledPipe())
    assert inspector_for(process, finder=lambda name: None).inspect_uv() == UvObservation(
        version=None, code=DiagnosticCode.MISSING_COMMAND
    )

    def missing_start(*args: object, **kwargs: object) -> LifecycleProcess:
        raise FileNotFoundError("SENTINEL native path")

    output = FixedCommandInspector(lambda name: r"C:\Tools\uv.exe", missing_start).inspect_uv()
    assert output == UvObservation(version=None, code=DiagnosticCode.MISSING_COMMAND)
    assert "SENTINEL" not in repr(output)


def test_start_permission_denied_is_safe() -> None:
    def denied(*args: object, **kwargs: object) -> LifecycleProcess:
        raise PermissionError("SENTINEL denied")

    output = FixedCommandInspector(lambda name: r"C:\Tools\uv.exe", denied).inspect_uv()
    assert output == UvObservation(version=None, code=DiagnosticCode.PERMISSION_DENIED)
    assert "SENTINEL" not in repr(output)


@pytest.mark.parametrize("stage", ["discovery", "start", "read", "poll", "wait", "terminate"])
def test_residual_oserror_is_redacted_and_cleanup_is_attempted(stage: str) -> None:
    sentinel = OSError(f"SENTINEL {stage} native detail")
    events: list[str] = []
    pipe = ControlledPipe(
        b"safe",
        read_error=sentinel if stage in {"read", "terminate"} else None,
        events=events,
    )
    running = stage == "terminate"
    errors = {stage: sentinel} if stage in {"poll", "wait", "terminate"} else {}
    process = LifecycleProcess(pipe, running=running, errors=errors)

    def find(name: str) -> str:
        if stage == "discovery":
            raise sentinel
        return r"C:\Tools\uv.exe"

    def start(*args: object, **kwargs: object) -> LifecycleProcess:
        if stage == "start":
            raise sentinel
        return process

    output = FixedCommandInspector(find, start, lambda: 0.0).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert "SENTINEL" not in repr(output)
    if stage not in {"discovery", "start"}:
        assert process.stdout.closed
        assert process.stdout.reader_finished.is_set()
        if stage != "terminate":
            assert process.reaped


def test_timeout_terminates_escalates_to_kill_reaps_closes_and_joins() -> None:
    events: list[str] = []
    stopped = threading.Event()
    pipe = ControlledPipe(block_until=stopped, events=events)
    process = LifecycleProcess(pipe, running=True, require_kill=True)
    times = iter([0.0, 6.0])

    output = inspector_for(process, clock=lambda: next(times)).inspect_uv()

    assert output == UvObservation(version=None, code=DiagnosticCode.TIMEOUT)
    assert process.terminated and process.killed
    assert events == ["terminate", "wait", "kill", "wait", "close_stdout"]
    assert_clean(process)


def test_nonzero_exit_is_command_failed_after_cleanup() -> None:
    process = LifecycleProcess(ControlledPipe(b"untrusted"), returncode=9)

    output = inspector_for(process).inspect_nvidia_output()

    assert output == CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
    assert_clean(process)


@pytest.mark.parametrize("raw", [b"not uv", b"uv \xff"])
def test_uv_parser_failures_happen_only_after_reap_close_and_reader_join(raw: bytes) -> None:
    events: list[str] = []
    process = LifecycleProcess(ControlledPipe(raw, events=events))

    output = inspector_for(process).inspect_uv()

    assert output == UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)
    assert events == ["wait", "close_stdout"]
    assert_clean(process)


def test_public_surface_has_no_arbitrary_runner() -> None:
    inspector = FixedCommandInspector(lambda name: None, subprocess.Popen)

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
    process = LifecycleProcess(ControlledPipe(b"uv 0.12.3"))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    observation = inspector_for(
        process, finder=lambda name: r"tools\uv.exe", calls=calls
    ).inspect_uv()

    assert observation == UvObservation(version="0.12.3")
    assert calls[0][0] == [os.path.abspath(r"tools\uv.exe"), "--version"]
