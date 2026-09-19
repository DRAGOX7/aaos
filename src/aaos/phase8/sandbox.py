"""Trusted isolated worker supervisor for Phase 8 computation."""

from __future__ import annotations

import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import IO, Any, Protocol, Sequence

from scripts.phase8.qualify_isolation_runtime import IsolationRuntimeProbe

from .computation_contracts import ComputationRequest, ComputationResult, ComputationStatus
from .sandbox_resources import DOCKER_CONTEXT, WORKER_IMAGE, docker_run_args, materialize_inputs
from .worker_protocol import decode_worker_result, encode_worker_request

_CLEANUP_OUTPUT_LIMIT_BYTES = 4096

_WORKER_SCRIPT = (
    "import json,resource,sys\n"
    "request=json.loads(sys.stdin.buffer.read())\n"
    "cpu=request['limits']['cpu_time_seconds']; resource.setrlimit(resource.RLIMIT_CPU,(cpu,cpu))\n"
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
    output_exceeded: bool = False


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
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
    WORKER_IMAGE_UNAVAILABLE = "WORKER_IMAGE_UNAVAILABLE"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class SandboxExecutionError(ValueError):
    """Closed supervisor failure without raw process/path details."""

    def __init__(self, reason_code: SandboxReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " ").lower())


class SubprocessRunner:
    """Structured subprocess adapter; shell execution is never enabled."""

    def run(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
    ) -> CommandResult:
        for name, limit in (
            ("stdout_limit_bytes", stdout_limit_bytes),
            ("stderr_limit_bytes", stderr_limit_bytes),
        ):
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError(f"{name} must be a positive integer")
        try:
            process = subprocess.Popen(
                list(args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError:
            return CommandResult(-1, b"", b"", started=False)

        terminated = threading.Event()
        output_exceeded = threading.Event()

        def terminate_once() -> None:
            if terminated.is_set():
                return
            terminated.set()
            try:
                process.kill()
            except OSError:
                pass

        def read_bounded(stream: IO[Any] | None, limit: int) -> bytes:
            if stream is None:
                return b""
            collected = bytearray()
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                remaining = limit - len(collected)
                if len(chunk) > remaining:
                    if remaining > 0:
                        collected.extend(chunk[:remaining])
                    output_exceeded.set()
                    terminate_once()
                    break
                collected.extend(chunk)
            return bytes(collected)

        stdout_holder: list[bytes] = []
        stderr_holder: list[bytes] = []
        stdout_thread = threading.Thread(
            target=lambda: stdout_holder.append(read_bounded(process.stdout, stdout_limit_bytes)),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=lambda: stderr_holder.append(read_bounded(process.stderr, stderr_limit_bytes)),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(input_bytes)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_once()
                process.wait(timeout=5.0)
        finally:
            if process.poll() is None:
                terminate_once()
                process.wait(timeout=5.0)
            stdout_thread.join(timeout=5.0)
            stderr_thread.join(timeout=5.0)
        return CommandResult(
            process.returncode if process.returncode is not None else -1,
            stdout_holder[0] if stdout_holder else b"",
            stderr_holder[0] if stderr_holder else b"",
            started=True,
            timed_out=timed_out,
            output_exceeded=output_exceeded.is_set(),
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
        if not self._worker_image_available():
            raise SandboxExecutionError(SandboxReasonCode.WORKER_IMAGE_UNAVAILABLE)
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
                    stdout_limit_bytes=request.limits.output_bytes,
                    stderr_limit_bytes=request.limits.output_bytes,
                )
            except Exception:
                duration_ms = int(max(0.0, self._clock.monotonic() - started_at) * 1000)
                return _unknown_result(request, duration_ms)
            elapsed_ms = int(max(0.0, self._clock.monotonic() - started_at) * 1000)
            if command.timed_out or command.output_exceeded:
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
            if not self._cleanup_worker(worker_name, workspace):
                raise SandboxExecutionError(SandboxReasonCode.CLEANUP_FAILED)

    def _worker_image_available(self) -> bool:
        try:
            result = self._runner.run(
                (
                    "docker",
                    "--context",
                    DOCKER_CONTEXT,
                    "image",
                    "inspect",
                    WORKER_IMAGE,
                ),
                input_bytes=b"",
                timeout_seconds=5.0,
                stdout_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
                stderr_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
            )
        except Exception:
            return False
        return (
            result.started
            and result.returncode == 0
            and not result.timed_out
            and not result.output_exceeded
        )

    def _cleanup_worker(self, worker_name: str, workspace: Path) -> bool:
        """Remove the worker and prove both container and workspace are absent."""

        try:
            remove = self._runner.run(
                ("docker", "--context", DOCKER_CONTEXT, "rm", "-f", worker_name),
                input_bytes=b"",
                timeout_seconds=5.0,
                stdout_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
                stderr_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
            )
            inspect = self._runner.run(
                ("docker", "--context", DOCKER_CONTEXT, "inspect", worker_name),
                input_bytes=b"",
                timeout_seconds=5.0,
                stdout_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
                stderr_limit_bytes=_CLEANUP_OUTPUT_LIMIT_BYTES,
            )
            container_absent = inspect.returncode != 0 and inspect.stdout.strip() in (b"", b"[]")
            try:
                shutil.rmtree(workspace)
            except OSError:
                return False
            workspace_absent = not workspace.exists()
            return (
                (remove.returncode == 0 or container_absent)
                and container_absent
                and workspace_absent
            )
        except Exception:
            return False


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
