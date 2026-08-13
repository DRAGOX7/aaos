from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aaos.doctor.models import CommandOutput, DiagnosticCode, UvObservation

COMMAND_TIMEOUT_SECONDS = 5.0
MAX_COMMAND_OUTPUT_BYTES = 16_384

_UV_ARGS = ("--version",)
_NVIDIA_ARGS = (
    "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
    "--format=csv,noheader,nounits",
)
_SAFE_ENVIRONMENT_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR")
_UV_VERSION_PATTERN = re.compile(r"uv[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:[ \t]+[^\r\n]*)?")

ExecutableFinder = Callable[[str], str | None]
ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


@runtime_checkable
class CommandInspector(Protocol):
    def inspect_uv(self) -> UvObservation: ...

    def inspect_nvidia_output(self) -> CommandOutput: ...


@dataclass(frozen=True, slots=True)
class _CommandSpecification:
    executable_name: str
    arguments: tuple[str, ...]


_UV_COMMAND = _CommandSpecification("uv", _UV_ARGS)
_NVIDIA_COMMAND = _CommandSpecification("nvidia-smi", _NVIDIA_ARGS)


def parse_uv_version(output: CommandOutput) -> UvObservation:
    if output.code is not None:
        return UvObservation(version=None, code=output.code)
    if output.text is None:
        return UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)

    match = _UV_VERSION_PATTERN.fullmatch(output.text.strip())
    if match is None:
        return UvObservation(version=None, code=DiagnosticCode.MALFORMED_OUTPUT)
    return UvObservation(version=match.group(1))


class FixedCommandInspector:
    def __init__(
        self,
        executable_finder: ExecutableFinder = shutil.which,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        self._executable_finder = executable_finder
        self._process_runner = process_runner

    def inspect_uv(self) -> UvObservation:
        return parse_uv_version(self._run(_UV_COMMAND))

    def inspect_nvidia_output(self) -> CommandOutput:
        return self._run(_NVIDIA_COMMAND)

    def _run(self, command: _CommandSpecification) -> CommandOutput:
        if command is not _UV_COMMAND and command is not _NVIDIA_COMMAND:
            raise ValueError("command specification is not module-owned")

        discovered_path = self._executable_finder(command.executable_name)
        if discovered_path is None:
            return CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)

        executable_path = os.path.abspath(discovered_path)
        arguments = [executable_path, *command.arguments]
        environment = {key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ}

        try:
            completed = self._process_runner(
                arguments,
                shell=False,
                capture_output=True,
                text=False,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
                env=environment,
            )
        except FileNotFoundError:
            return CommandOutput(text=None, code=DiagnosticCode.MISSING_COMMAND)
        except PermissionError:
            return CommandOutput(text=None, code=DiagnosticCode.PERMISSION_DENIED)
        except subprocess.TimeoutExpired:
            return CommandOutput(text=None, code=DiagnosticCode.TIMEOUT)

        stdout = completed.stdout
        stderr = completed.stderr
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            return CommandOutput(text=None, code=DiagnosticCode.MALFORMED_OUTPUT)
        if len(stdout) > MAX_COMMAND_OUTPUT_BYTES or len(stderr) > MAX_COMMAND_OUTPUT_BYTES:
            return CommandOutput(text=None, code=DiagnosticCode.OVERSIZED_OUTPUT)
        if completed.returncode != 0:
            return CommandOutput(text=None, code=DiagnosticCode.COMMAND_FAILED)

        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return CommandOutput(text=None, code=DiagnosticCode.MALFORMED_OUTPUT)
        return CommandOutput(text=text)
