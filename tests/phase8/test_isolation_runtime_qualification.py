from __future__ import annotations

import os

import pytest

from scripts.phase8.qualify_isolation_runtime import (
    IsolationRuntimeProbe,
    assess_runtime_profile,
    qualify_isolation_runtime,
)


def test_incomplete_runtime_profile_is_rejected() -> None:
    probe = assess_runtime_profile(
        {
            "runtime_id": "docker",
            "network_disabled": False,
            "read_only_root": True,
            "credentials_absent": True,
            "task_mount_only": True,
            "resource_limits_supported": True,
        }
    )

    assert probe.available is False
    assert probe.reason_code == "REQUIRED_ISOLATION_PROPERTY_MISSING"


def test_complete_runtime_profile_is_accepted() -> None:
    probe = assess_runtime_profile(
        {
            "runtime_id": "docker-desktop-linux",
            "network_disabled": True,
            "read_only_root": True,
            "credentials_absent": True,
            "task_mount_only": True,
            "resource_limits_supported": True,
        }
    )

    assert probe == IsolationRuntimeProbe(
        runtime_id="docker-desktop-linux",
        network_disabled=True,
        read_only_root=True,
        credentials_absent=True,
        task_mount_only=True,
        resource_limits_supported=True,
        available=True,
        reason_code="QUALIFIED",
    )


def test_unapproved_docker_context_is_rejected() -> None:
    probe = qualify_isolation_runtime(docker_context="default")

    assert probe.available is False
    assert probe.reason_code == "CONTEXT_NOT_APPROVED"


@pytest.mark.phase8_runtime
@pytest.mark.skipif(
    os.environ.get("AAOS_PHASE8_RUN_REAL_QUALIFICATION") != "1",
    reason="set AAOS_PHASE8_RUN_REAL_QUALIFICATION=1 for the explicit local runtime probe",
)
def test_local_docker_runtime_qualification() -> None:
    probe = qualify_isolation_runtime(docker_context="desktop-linux")

    assert probe.available is True
    assert probe.runtime_id == "docker-desktop-linux"
    assert probe.network_disabled is True
    assert probe.read_only_root is True
    assert probe.credentials_absent is True
    assert probe.task_mount_only is True
    assert probe.resource_limits_supported is True
