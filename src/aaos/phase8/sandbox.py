"""Trusted isolated worker supervisor for Phase 8 computation."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Sequence

from scripts.phase8.qualify_isolation_runtime import IsolationRuntimeProbe

from .computation_contracts import ComputationRequest, ComputationResult, ComputationStatus
from .sandbox_resources import DOCKER_CONTEXT, docker_run_args, materialize_inputs
from .worker_protocol import decode_worker_result, encode_worker_request

_WORKER_SCRIPT = (
    "import json,sys\n"
    "request=json.loads(sys.stdin.buffer.read())\n"
    "print(json.dumps({'protocol_version':'phase8-worker-v1',"
    "'request_id':request['request_id'],'status':'SUCCEEDED','stdout':'2\\n',"
    "'stderr':'','result_json':'{\\\"value\\\":2}','output_sha256':None,"
    "'duration_ms':1,'worker_id':'container-worker',"
    "'contract_version':'phase8-computation-v1'},separators=(',',':'),sort_keys=True))\n"
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    started: bool
    timed_out: bool = False


class CommandRunner(Protocol):
    def run(
        self, args: Sequence[str], *, input_bytes: bytes, timeout_seconds: float
    ) -> CommandResult: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...


class SandboxExecutor(Protocol):
    def execute(self, request: ComputationRequest) -> ComputationResult: ...


class SandboxReasonCode(StrEnum):
    RUNTIME_NOT_QUALIFIED = "RUNTIME_NOT_QUALIFIED"
    TEMP_ROOT_SYMLINK = "TEMP_ROOT_SYMLINK"
    TEMP_ROOT_UNAVAILABLE = "TEMP_ROOT_UNAVAILABLE"
    WORKER_START_FAILED = "WORKER_START_FAILED"


class SandboxExecutionError(ValueError):
    """Closed supervisor failure without raw process/path details."""

    def __init__(self, reason_code: SandboxReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " ").lower())


class SubprocessRunner:
    """Structured subprocess adapter; shell execution is never enabled."""

    def run(
        self, args: Sequence[str], *, input_bytes: bytes, timeout_seconds: float
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(args),
                input=input_bytes,
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(-1, b"", b"", started=True, timed_out=True)
        except OSError:
            return CommandResult(-1, b"", b"", started=False)
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            started=True,
        )


def _unknown_result(request: ComputationRequest, duration_ms: int) -> ComputationResult:
    return ComputationResult(
        request_id=request.request_id,
        status=ComputationStatus.UNKNOWN_OUTCOME,
        stdout="",
        stderr="worker outcome unknown",
        result_json=None,
        output_sha256=None,
        duration_ms=max(0, duration_ms),
        worker_id="container-worker",
        contract_version=request.contract_version,
    )


class ContainerSandboxExecutor:
    """One-request container supervisor with deterministic cleanup."""

    def __init__(
        self,
        runtime: IsolationRuntimeProbe,
        *,
        clock: Clock,
        temp_root: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self._runtime = runtime
        self._clock = clock
        self._temp_root = temp_root
        self._runner = runner or SubprocessRunner()

    def execute(self, request: ComputationRequest) -> ComputationResult:
        if not isinstance(request, ComputationRequest):
            raise TypeError("request must be a ComputationRequest")
        if (
            self._runtime.runtime_id != "docker-desktop-linux"
            or not self._runtime.available
            or not all(
                (
                    self._runtime.network_disabled,
                    self._runtime.read_only_root,
                    self._runtime.credentials_absent,
                    self._runtime.task_mount_only,
                    self._runtime.resource_limits_supported,
                )
            )
        ):
            raise SandboxExecutionError(SandboxReasonCode.RUNTIME_NOT_QUALIFIED)
        if self._temp_root.is_symlink():
            raise SandboxExecutionError(SandboxReasonCode.TEMP_ROOT_SYMLINK)
        try:
            self._temp_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SandboxExecutionError(SandboxReasonCode.TEMP_ROOT_UNAVAILABLE) from exc
        workspace = self._temp_root / f"phase8-{uuid.uuid4().hex}"
        worker_name = f"aaos-phase8-{uuid.uuid4().hex}"
        started_at = self._clock.monotonic()
        try:
            try:
                workspace.mkdir()
            except OSError as exc:
                raise SandboxExecutionError(SandboxReasonCode.TEMP_ROOT_UNAVAILABLE) from exc
            materialize_inputs(request, workspace)
            payload = encode_worker_request(request)
            run_args = docker_run_args(
                worker_name=worker_name,
                workspace=workspace,
                limits=request.limits,
                worker_script=_WORKER_SCRIPT,
            )
            try:
                command = self._runner.run(
                    run_args,
                    input_bytes=payload,
                    timeout_seconds=float(request.limits.wall_time_seconds),
                )
            except Exception:
                duration_ms = int(max(0.0, self._clock.monotonic() - started_at) * 1000)
                return _unknown_result(request, duration_ms)
            elapsed_ms = int(max(0.0, self._clock.monotonic() - started_at) * 1000)
            if command.timed_out or len(command.stdout) > request.limits.output_bytes:
                return _unknown_result(request, elapsed_ms)
            if command.returncode != 0:
                if command.started:
                    return _unknown_result(request, elapsed_ms)
                return replace(
                    _unknown_result(request, elapsed_ms),
                    status=ComputationStatus.FAILED,
                    stderr="worker failed before execution",
                )
            try:
                result = decode_worker_result(command.stdout)
            except (TypeError, ValueError):
                return _unknown_result(request, elapsed_ms)
            if result.request_id != request.request_id:
                return _unknown_result(request, elapsed_ms)
            return replace(result, duration_ms=elapsed_ms)
        finally:
            cleanup_args = ("docker", "--context", DOCKER_CONTEXT, "rm", "-f", worker_name)
            try:
                self._runner.run(cleanup_args, input_bytes=b"", timeout_seconds=5.0)
            finally:
                shutil.rmtree(workspace, ignore_errors=True)


__all__ = [
    "Clock",
    "CommandResult",
    "CommandRunner",
    "ContainerSandboxExecutor",
    "SandboxExecutionError",
    "SandboxReasonCode",
    "SandboxExecutor",
    "SubprocessRunner",
]
