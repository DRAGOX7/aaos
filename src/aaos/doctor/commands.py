from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast, runtime_checkable

from aaos.doctor.models import CommandOutput, DiagnosticCode, UvObservation

COMMAND_TIMEOUT_SECONDS = 5.0
MAX_COMMAND_OUTPUT_BYTES = 16_384

UV_ARGS = ("--version",)
NVIDIA_ARGS = (
    "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
    "--format=csv,noheader,nounits",
)

_SAFE_ENVIRONMENT_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR")
_READER_POLL_SECONDS = 0.01
_TERMINATION_GRACE_SECONDS = 0.1
_UV_VERSION_PATTERN = re.compile(r"uv[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:[ \t]+[^\r\n]*)?")


class _BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _Process(Protocol):
    stdout: _BinaryReader | None
    returncode: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ExecutableFinder = Callable[[str], str | None]
ProcessFactory = Callable[..., object]
MonotonicClock = Callable[[], float]


@runtime_checkable
class CommandInspector(Protocol):
    def inspect_uv(self) -> UvObservation: ...

    def inspect_nvidia_output(self) -> CommandOutput: ...


@dataclass(frozen=True, slots=True)
class _CommandSpec:
    executable_name: str
    arguments: tuple[str, ...]


_UV_SPEC = _CommandSpec("uv", UV_ARGS)
_NVIDIA_SPEC = _CommandSpec("nvidia-smi", NVIDIA_ARGS)


@dataclass(slots=True)
class _ReaderState:
    output: bytearray = field(default_factory=bytearray)
    done: threading.Event = field(default_factory=threading.Event)
    overflow: threading.Event = field(default_factory=threading.Event)
    failed: threading.Event = field(default_factory=threading.Event)


def parse_uv_version(output: CommandOutput) -> UvObservation:
    if output.code is not None:
        return UvObservation(version=None, code=output.code)
    if output.text is None:
        return UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)

    match = _UV_VERSION_PATTERN.fullmatch(output.text.strip())
    if match is None:
        return UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)
    return UvObservation(version=match.group(1))


def _read_bounded_stdout(stdout: _BinaryReader, state: _ReaderState) -> None:
    try:
        while True:
            remaining = MAX_COMMAND_OUTPUT_BYTES - len(state.output)
            chunk = stdout.read(remaining + 1)
            if not chunk:
                return
            state.output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                state.overflow.set()
                return
    except OSError:
        state.failed.set()
    finally:
        state.done.set()


def _wait_for_exit(process: _Process) -> bool:
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False


def _stop_and_reap(process: _Process, *, stop_requested: bool) -> bool:
    clean = True
    if stop_requested:
        try:
            process.terminate()
        except OSError:
            clean = False
        if not _wait_for_exit(process):
            try:
                process.kill()
            except OSError:
                clean = False
            if not _wait_for_exit(process):
                clean = False
                try:
                    process.wait()
                except OSError:
                    clean = False
    elif not _wait_for_exit(process):
        clean = False
        try:
            process.kill()
        except OSError:
            clean = False
        if not _wait_for_exit(process):
            try:
                process.wait()
            except OSError:
                clean = False
    return clean


class FixedCommandInspector:
    def __init__(
        self,
        executable_finder: ExecutableFinder = shutil.which,
        process_factory: ProcessFactory = subprocess.Popen,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._executable_finder = executable_finder
        self._process_factory = process_factory
        self._monotonic_clock = monotonic_clock

    def inspect_uv(self) -> UvObservation:
        return parse_uv_version(self._run(_UV_SPEC))

    def inspect_nvidia_output(self) -> CommandOutput:
        return self._run(_NVIDIA_SPEC)

    def _run(self, command: _CommandSpec) -> CommandOutput:
        if command is not _UV_SPEC and command is not _NVIDIA_SPEC:
            return CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)

        try:
            discovered_path = self._executable_finder(command.executable_name)
        except FileNotFoundError:
            return CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)
        except PermissionError:
            return CommandOutput(text=None, code=DiagnosticCode.PERMISSION_DENIED)
        except OSError:
            return CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)
        if discovered_path is None:
            return CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)

        arguments = [os.path.abspath(discovered_path), *command.arguments]
        environment = {key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ}
        try:
            process = cast(
                _Process,
                self._process_factory(
                    arguments,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=False,
                    env=environment,
                ),
            )
        except FileNotFoundError:
            return CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)
        except PermissionError:
            return CommandOutput(text=None, code=DiagnosticCode.PERMISSION_DENIED)
        except OSError:
            return CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)

        stdout = process.stdout
        if stdout is None:
            _stop_and_reap(process, stop_requested=True)
            return CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)

        state = _ReaderState()
        reader = threading.Thread(
            target=_read_bounded_stdout,
            args=(stdout, state),
            name="aaos-doctor-stdout",
        )
        reader.start()
        deadline = self._monotonic_clock() + COMMAND_TIMEOUT_SECONDS
        code: DiagnosticCode | None = None
        returncode: int | None = None

        while code is None:
            if state.overflow.is_set():
                code = DiagnosticCode.OVERSIZED_OUTPUT
                break
            if state.failed.is_set():
                code = DiagnosticCode.COMMAND_FAILED
                break
            try:
                returncode = process.poll()
            except OSError:
                code = DiagnosticCode.COMMAND_FAILED
                break
            if returncode is not None and state.done.is_set():
                break
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                code = DiagnosticCode.TIMEOUT
                break
            state.done.wait(min(_READER_POLL_SECONDS, remaining))

        cleanup_ok = _stop_and_reap(process, stop_requested=code is not None)
        try:
            stdout.close()
        except OSError:
            cleanup_ok = False
        reader.join()

        if not cleanup_ok:
            code = DiagnosticCode.COMMAND_FAILED
        elif state.failed.is_set():
            code = DiagnosticCode.COMMAND_FAILED
        elif state.overflow.is_set():
            code = DiagnosticCode.OVERSIZED_OUTPUT
        elif code is None and returncode != 0:
            code = DiagnosticCode.COMMAND_FAILED
        if code is not None:
            return CommandOutput(text=None, code=code)

        try:
            text = bytes(state.output).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return CommandOutput(text=None, code=DiagnosticCode.MALFORMED_OUTPUT)
        return CommandOutput(text=text)
