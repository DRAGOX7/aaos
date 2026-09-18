from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

APPROVED_DOCKER_CONTEXT = "desktop-linux"
PROBE_IMAGE = "python:3.11-slim"


@dataclass(frozen=True)
class IsolationRuntimeProbe:
    runtime_id: str
    network_disabled: bool
    read_only_root: bool
    credentials_absent: bool
    task_mount_only: bool
    resource_limits_supported: bool
    available: bool
    reason_code: str


def assess_runtime_profile(profile: Mapping[str, object]) -> IsolationRuntimeProbe:
    required = (
        "network_disabled",
        "read_only_root",
        "credentials_absent",
        "task_mount_only",
        "resource_limits_supported",
    )
    runtime_id = profile.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id:
        return IsolationRuntimeProbe(
            runtime_id="unknown",
            network_disabled=False,
            read_only_root=False,
            credentials_absent=False,
            task_mount_only=False,
            resource_limits_supported=False,
            available=False,
            reason_code="RUNTIME_ID_MISSING",
        )

    values = {name: profile.get(name) is True for name in required}
    available = all(values.values())
    return IsolationRuntimeProbe(
        runtime_id=runtime_id,
        network_disabled=values["network_disabled"],
        read_only_root=values["read_only_root"],
        credentials_absent=values["credentials_absent"],
        task_mount_only=values["task_mount_only"],
        resource_limits_supported=values["resource_limits_supported"],
        available=available,
        reason_code="QUALIFIED" if available else "REQUIRED_ISOLATION_PROPERTY_MISSING",
    )


def _run(args: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _unavailable(reason_code: str) -> IsolationRuntimeProbe:
    return IsolationRuntimeProbe(
        runtime_id="unknown",
        network_disabled=False,
        read_only_root=False,
        credentials_absent=False,
        task_mount_only=False,
        resource_limits_supported=False,
        available=False,
        reason_code=reason_code,
    )


def _json_output(completed: subprocess.CompletedProcess[str]) -> Mapping[str, object] | None:
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def qualify_isolation_runtime(
    *, docker_context: str = APPROVED_DOCKER_CONTEXT
) -> IsolationRuntimeProbe:
    if docker_context != APPROVED_DOCKER_CONTEXT:
        return _unavailable("CONTEXT_NOT_APPROVED")

    image = PROBE_IMAGE
    info = _run(("docker", "--context", docker_context, "info", "--format", "{{json .}}"))
    if info.returncode != 0:
        reason = "PERMISSION_FAILURE" if "permission denied" in info.stderr.lower() else "DOCKER_ENGINE_UNAVAILABLE"
        return _unavailable(reason)

    server = _json_output(info)
    if server is None or server.get("OSType") != "linux":
        return _unavailable("LINUX_CONTAINERS_UNAVAILABLE")

    image_check = _run(("docker", "--context", docker_context, "image", "inspect", image))
    if image_check.returncode != 0:
        return _unavailable("LOCAL_PROBE_IMAGE_MISSING")

    container_name = f"aaos-phase8-task0-{uuid.uuid4().hex}"
    probe_script = (
        "import json, os, pathlib, socket, urllib.request\n"
        "result = {}\n"
        "result['network_disabled'] = False\n"
        "result['credentials_absent'] = os.environ.get('AAOS_PHASE8_HOST_SECRET') is None\n"
        "result['host_paths_absent'] = not pathlib.Path('/mnt/c').exists() and not pathlib.Path('/workspace').exists()\n"
        "result['docker_socket_absent'] = not pathlib.Path('/var/run/docker.sock').exists()\n"
        "result['device_absent'] = not any(pathlib.Path('/dev').glob('nvidia*'))\n"
        "result['non_root'] = os.getuid() != 0\n"
        "blocked = 0\n"
        "try:\n"
        "    urllib.request.urlopen('http://198.51.100.1', timeout=1)\n"
        "except Exception:\n"
        "    blocked += 1\n"
        "try:\n"
        "    socket.getaddrinfo('invalid.phase8.test', 80)\n"
        "except Exception:\n"
        "    blocked += 1\n"
        "result['network_disabled'] = blocked == 2\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )
    create_args = (
        "docker",
        "--context",
        docker_context,
        "create",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "128m",
        "--cpus",
        "0.25",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--user",
        "65532:65532",
        image,
        "python",
        "-c",
        probe_script,
    )
    created = _run(create_args)
    if created.returncode != 0:
        reason = "PERMISSION_FAILURE" if "permission denied" in created.stderr.lower() else "RUNTIME_PROBE_FAILED"
        return _unavailable(reason)

    try:
        inspect = _run(("docker", "--context", docker_context, "inspect", container_name))
        # `docker inspect` returns a list; the helper accepts a mapping only,
        # so the raw JSON is decoded explicitly here.
        raw_details = json.loads(inspect.stdout)
        if not isinstance(raw_details, list) or not raw_details or not isinstance(raw_details[0], dict):
            return _unavailable("RUNTIME_PROFILE_UNREADABLE")
        container = raw_details[0]
        host_config = container.get("HostConfig")
        config = container.get("Config")
        if not isinstance(host_config, dict) or not isinstance(config, dict):
            return _unavailable("RUNTIME_PROFILE_UNREADABLE")
        security = host_config.get("SecurityOpt")
        cap_drop = host_config.get("CapDrop")
        devices = host_config.get("Devices")
        device_requests = host_config.get("DeviceRequests")
        profile = {
            "runtime_id": "docker-desktop-linux",
            "network_disabled": host_config.get("NetworkMode") == "none",
            "read_only_root": host_config.get("ReadonlyRootfs") is True,
            "credentials_absent": True,
            "task_mount_only": container.get("Mounts") in ([], None)
            and device_requests in ([], None)
            and devices in ([], None),
            "resource_limits_supported": (
                isinstance(host_config.get("PidsLimit"), int)
                and host_config.get("PidsLimit", 0) > 0
                and isinstance(host_config.get("Memory"), int)
                and host_config.get("Memory", 0) > 0
                and isinstance(host_config.get("NanoCpus"), int)
                and host_config.get("NanoCpus", 0) > 0
            ),
        }
        if security != ["no-new-privileges"] or cap_drop != ["ALL"]:
            return _unavailable("RUNTIME_PRIVILEGE_PROFILE_INVALID")
        if host_config.get("Privileged") is True or config.get("User") != "65532:65532":
            return _unavailable("RUNTIME_PRIVILEGE_PROFILE_INVALID")
        started = _run(("docker", "--context", docker_context, "start", container_name))
        if started.returncode != 0:
            return _unavailable("RUNTIME_PROBE_FAILED")
        waited = _run(("docker", "--context", docker_context, "wait", container_name))
        logs = _run(("docker", "--context", docker_context, "logs", container_name))
        output = _json_output(logs)
        if waited.returncode != 0 or output is None:
            return _unavailable("RUNTIME_PROBE_FAILED")
        profile["network_disabled"] = profile["network_disabled"] and output.get("network_disabled") is True
        profile["credentials_absent"] = output.get("credentials_absent") is True
        profile["task_mount_only"] = (
            profile["task_mount_only"]
            and output.get("host_paths_absent") is True
            and output.get("docker_socket_absent") is True
            and output.get("device_absent") is True
            and output.get("non_root") is True
        )
        return assess_runtime_profile(profile)
    finally:
        _run(("docker", "--context", docker_context, "rm", "-f", container_name))


def main() -> int:
    probe = qualify_isolation_runtime()
    print(json.dumps(asdict(probe), sort_keys=True))
    return 0 if probe.available else 1


if __name__ == "__main__":
    raise SystemExit(main())
