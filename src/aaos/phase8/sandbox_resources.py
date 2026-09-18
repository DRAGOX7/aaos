"""Resource-profile and task-directory helpers for the Phase 8 supervisor."""

from __future__ import annotations

from pathlib import Path

from .computation_contracts import ComputationLimits, ComputationRequest

DOCKER_CONTEXT = "desktop-linux"
WORKER_IMAGE = "python:3.11-slim"
WORKER_UID = "65532:65532"


def docker_run_args(
    *, worker_name: str, workspace: Path, limits: ComputationLimits, worker_script: str
) -> tuple[str, ...]:
    """Build the fixed least-authority Docker command for one worker."""

    source = str(workspace.resolve())
    return (
        "docker",
        "--context",
        DOCKER_CONTEXT,
        "run",
        "--rm",
        "--name",
        worker_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(limits.max_processes),
        "--memory",
        f"{limits.memory_bytes}b",
        "--cpus",
        "0.25",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--tmpfs",
        f"/task-output:rw,noexec,nosuid,size={limits.output_bytes}b",
        "--user",
        WORKER_UID,
        "--mount",
        f"type=bind,source={source},target=/task-input,readonly",
        WORKER_IMAGE,
        "python",
        "-c",
        worker_script,
    )


def materialize_inputs(request: ComputationRequest, workspace: Path) -> tuple[Path, ...]:
    """Write only validated bytes beneath a fresh, task-local directory."""

    if not isinstance(request, ComputationRequest):
        raise TypeError("request must be a ComputationRequest")
    if not isinstance(workspace, Path):
        raise TypeError("workspace must be a Path")
    if workspace.is_symlink():
        raise ValueError("workspace must not be a symlink")
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError("workspace must be an existing directory")
    root = workspace.resolve()
    paths: list[Path] = []
    for item in request.inputs:
        path = workspace / f"input_{item.input_id}.bin"
        if path.exists() or path.is_symlink():
            raise ValueError("input destination already exists")
        resolved = path.resolve(strict=False)
        if root not in resolved.parents:
            raise ValueError("input path escapes workspace")
        with path.open("xb") as handle:
            handle.write(item.bounded_bytes)
        paths.append(path)
    return tuple(paths)


__all__ = [
    "DOCKER_CONTEXT",
    "WORKER_IMAGE",
    "WORKER_UID",
    "docker_run_args",
    "materialize_inputs",
]
