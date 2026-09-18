from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Sequence

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationStatus,
)
from aaos.phase8.sandbox import (
    CommandResult,
    ContainerSandboxExecutor,
    SandboxExecutionError,
)
from aaos.phase8.worker_protocol import WORKER_PROTOCOL_VERSION
from scripts.phase8.qualify_isolation_runtime import IsolationRuntimeProbe


def _request() -> ComputationRequest:
    content = b"input"
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(ComputationInput("input-1", "text/plain", sha256(content).hexdigest(), content),),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def _runtime(available: bool = True) -> IsolationRuntimeProbe:
    return IsolationRuntimeProbe(
        runtime_id="docker-desktop-linux",
        network_disabled=available,
        read_only_root=available,
        credentials_absent=available,
        task_mount_only=available,
        resource_limits_supported=available,
        available=available,
        reason_code="QUALIFIED" if available else "DOCKER_ENGINE_UNAVAILABLE",
    )


def _payload(request_id: str = "request-1") -> bytes:
    return json.dumps(
        {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "request_id": request_id,
            "status": "SUCCEEDED",
            "stdout": "2\n",
            "stderr": "",
            "result_json": '{"value":2}',
            "output_sha256": None,
            "duration_ms": 1,
            "worker_id": "container-worker",
            "contract_version": COMPUTATION_CONTRACT_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 0.01
        return self.value


class FakeRunner:
    def __init__(self, *, timed_out: bool = False, stdout: bytes | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes]] = []
        self.timed_out = timed_out
        self.stdout = _payload() if stdout is None else stdout

    def run(
        self, args: Sequence[str], *, input_bytes: bytes, timeout_seconds: float
    ) -> CommandResult:
        self.calls.append((tuple(args), input_bytes))
        if "rm" in args:
            return CommandResult(returncode=0, stdout=b"", stderr=b"", started=True)
        if self.timed_out:
            return CommandResult(
                returncode=-1,
                stdout=b"",
                stderr=b"",
                started=True,
                timed_out=True,
            )
        return CommandResult(returncode=0, stdout=self.stdout, stderr=b"", started=True)


def test_executor_uses_qualified_profile_and_cleans_workspace(tmp_path: Path) -> None:
    runner = FakeRunner()
    executor = ContainerSandboxExecutor(
        _runtime(), clock=FakeClock(), temp_root=tmp_path, runner=runner
    )
    result = executor.execute(_request())

    assert result.status is ComputationStatus.SUCCEEDED
    execution_args = runner.calls[0][0]
    assert execution_args[:4] == ("docker", "--context", "desktop-linux", "run")
    assert "--network" in execution_args and "none" in execution_args
    assert "--read-only" in execution_args
    assert execution_args.count("--cap-drop") == 1
    assert "ALL" in execution_args
    assert "--security-opt" in execution_args
    assert "no-new-privileges" in execution_args
    assert "--pids-limit" in execution_args
    assert "--memory" in execution_args
    assert "--cpus" in execution_args
    assert "--device" not in execution_args
    assert "--privileged" not in execution_args
    assert "--env" not in execution_args
    assert "--volume" not in execution_args
    assert "docker.sock" not in " ".join(execution_args)
    assert "--net" not in execution_args
    assert not list(tmp_path.iterdir())


def test_unqualified_runtime_fails_closed_without_running_worker(tmp_path: Path) -> None:
    runner = FakeRunner()
    executor = ContainerSandboxExecutor(
        _runtime(False), clock=FakeClock(), temp_root=tmp_path, runner=runner
    )
    with pytest.raises(SandboxExecutionError, match="runtime"):
        executor.execute(_request())
    assert runner.calls == []


def test_timeout_returns_unknown_outcome_without_retry(tmp_path: Path) -> None:
    runner = FakeRunner(timed_out=True)
    executor = ContainerSandboxExecutor(
        _runtime(), clock=FakeClock(), temp_root=tmp_path, runner=runner
    )
    result = executor.execute(_request())

    assert result.status is ComputationStatus.UNKNOWN_OUTCOME
    execution_calls = [call for call in runner.calls if "run" in call[0]]
    assert len(execution_calls) == 1
    assert not list(tmp_path.iterdir())


def test_oversized_worker_output_is_unknown_and_not_retried(tmp_path: Path) -> None:
    runner = FakeRunner(stdout=b"x" * 5000)
    executor = ContainerSandboxExecutor(
        _runtime(), clock=FakeClock(), temp_root=tmp_path, runner=runner
    )
    result = executor.execute(_request())

    assert result.status is ComputationStatus.UNKNOWN_OUTCOME
    assert len([call for call in runner.calls if "run" in call[0]]) == 1


def test_each_request_gets_a_fresh_task_and_worker_identity(tmp_path: Path) -> None:
    runner = FakeRunner()
    executor = ContainerSandboxExecutor(
        _runtime(), clock=FakeClock(), temp_root=tmp_path, runner=runner
    )

    assert executor.execute(_request()).status is ComputationStatus.SUCCEEDED
    assert executor.execute(_request()).status is ComputationStatus.SUCCEEDED

    executions = [call for call in runner.calls if "run" in call[0]]
    assert len(executions) == 2
    assert (
        executions[0][0][executions[0][0].index("--name") + 1]
        != executions[1][0][executions[1][0].index("--name") + 1]
    )
    assert not list(tmp_path.iterdir())
