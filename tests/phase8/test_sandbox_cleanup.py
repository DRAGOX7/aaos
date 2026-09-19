from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path
from typing import Sequence

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
)
from aaos.phase8.sandbox import (
    CommandResult,
    ContainerSandboxExecutor,
    SandboxExecutionError,
)
from aaos.phase8.sandbox_resources import materialize_inputs
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


def _runtime() -> IsolationRuntimeProbe:
    return IsolationRuntimeProbe(
        runtime_id="docker-desktop-linux",
        network_disabled=True,
        read_only_root=True,
        credentials_absent=True,
        task_mount_only=True,
        resource_limits_supported=True,
        available=True,
        reason_code="QUALIFIED",
    )


def _result_payload() -> bytes:
    return (
        '{"contract_version":"phase8-computation-v1","duration_ms":1,'
        '"output_sha256":null,"protocol_version":"phase8-worker-v1",'
        '"request_id":"request-1","result_json":"{\\"value\\":2}",'
        '"status":"SUCCEEDED","stderr":"","stdout":"2\\n",'
        '"worker_id":"container-worker"}'
    ).encode()


class CleanupFailureRunner:
    def run(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        stdout_limit_bytes: int = 4096,
        stderr_limit_bytes: int = 4096,
    ) -> CommandResult:
        if "image" in args and "inspect" in args:
            return CommandResult(0, b"[]", b"", started=True)
        if "run" in args:
            return CommandResult(0, _result_payload(), b"", started=True)
        if "rm" in args:
            return CommandResult(1, b"", b"container remains", started=True)
        if "inspect" in args:
            return CommandResult(0, b"still-present", b"", started=True)
        return CommandResult(1, b"", b"", started=False)


class FixedClock:
    def monotonic(self) -> float:
        return 0.0


def test_materialization_is_bounded_and_uses_task_local_names(tmp_path: Path) -> None:
    paths = materialize_inputs(_request(), tmp_path)
    assert len(paths) == 1
    assert paths[0].parent == tmp_path
    assert paths[0].name == "input_input-1.bin"
    assert paths[0].read_bytes() == b"input"


def test_materialization_rejects_symlinked_workspace(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        materialize_inputs(_request(), link)


def test_computation_success_does_not_hide_failed_container_cleanup(tmp_path: Path) -> None:
    executor = ContainerSandboxExecutor(
        _runtime(),
        clock=FixedClock(),
        temp_root=tmp_path,
        runner=CleanupFailureRunner(),
    )
    with pytest.raises(SandboxExecutionError, match="cleanup"):
        executor.execute(_request())


def test_workspace_cleanup_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "rmtree", lambda path: None)
    executor = ContainerSandboxExecutor(
        _runtime(),
        clock=FixedClock(),
        temp_root=tmp_path,
        runner=CleanupFailureRunner(),
    )
    with pytest.raises(SandboxExecutionError, match="cleanup"):
        executor.execute(_request())
