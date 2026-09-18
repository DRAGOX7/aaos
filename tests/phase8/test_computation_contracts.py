from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from typing import Any, cast

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)


def _limits() -> ComputationLimits:
    return ComputationLimits(
        wall_time_seconds=5,
        cpu_time_seconds=5,
        memory_bytes=64 * 1024 * 1024,
        output_bytes=4096,
        max_processes=2,
        max_input_bytes=4096,
    )


def _input(content: bytes = b"hello") -> ComputationInput:
    return ComputationInput(
        input_id="input-1",
        media_type="text/plain",
        content_sha256=sha256(content).hexdigest(),
        bounded_bytes=content,
    )


def _request() -> ComputationRequest:
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(_input(),),
        limits=_limits(),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def test_contracts_are_frozen_and_have_closed_fields() -> None:
    request = _request()
    with pytest.raises(FrozenInstanceError):
        request.code = "danger"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ComputationInput(
            input_id="input-1",
            media_type="text/plain",
            content_sha256=sha256(b"hello").hexdigest(),
            bounded_bytes=b"hello",
            **cast(Any, {"path": "C:/secret"}),
        )


def test_input_sha256_and_media_type_are_closed() -> None:
    with pytest.raises(ValueError, match="content_sha256"):
        ComputationInput(
            input_id="input-1",
            media_type="text/plain",
            content_sha256="0" * 64,
            bounded_bytes=b"hello",
        )
    with pytest.raises(ValueError, match="media_type"):
        ComputationInput(
            input_id="input-1",
            media_type=cast(Any, "application/octet-stream"),
            content_sha256=sha256(b"hello").hexdigest(),
            bounded_bytes=b"hello",
        )


def test_limits_reject_bool_and_unbounded_values() -> None:
    with pytest.raises(TypeError):
        ComputationLimits(
            wall_time_seconds=cast(Any, True),
            cpu_time_seconds=5,
            memory_bytes=64 * 1024 * 1024,
            output_bytes=4096,
            max_processes=2,
            max_input_bytes=4096,
        )
    with pytest.raises(ValueError, match="memory_bytes"):
        ComputationLimits(
            wall_time_seconds=5,
            cpu_time_seconds=5,
            memory_bytes=512 * 1024 * 1024,
            output_bytes=4096,
            max_processes=2,
            max_input_bytes=4096,
        )


def test_request_rejects_mutable_inputs_unknown_version_and_oversized_input() -> None:
    with pytest.raises(TypeError, match="tuple"):
        ComputationRequest(
            request_id="request-1",
            code="1 + 1",
            inputs=cast(Any, [_input()]),
            limits=_limits(),
            contract_version=COMPUTATION_CONTRACT_VERSION,
        )
    with pytest.raises(ValueError, match="contract_version"):
        ComputationRequest(
            request_id="request-1",
            code="1 + 1",
            inputs=(_input(),),
            limits=_limits(),
            contract_version="phase8-computation-v0",
        )
    oversized = ComputationInput(
        input_id="input-1",
        media_type="text/plain",
        content_sha256=sha256(b"x" * 5000).hexdigest(),
        bounded_bytes=b"x" * 5000,
    )
    with pytest.raises(ValueError, match="inputs exceed"):
        ComputationRequest(
            request_id="request-1",
            code="1 + 1",
            inputs=(oversized,),
            limits=_limits(),
            contract_version=COMPUTATION_CONTRACT_VERSION,
        )


def test_result_status_is_closed_and_bounded() -> None:
    result = ComputationResult(
        request_id="request-1",
        status=ComputationStatus.UNKNOWN_OUTCOME,
        stdout="",
        stderr="",
        result_json=None,
        output_sha256=None,
        duration_ms=1,
        worker_id="worker-1",
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )
    assert result.status is ComputationStatus.UNKNOWN_OUTCOME
    with pytest.raises(ValueError, match="status"):
        ComputationResult(
            request_id="request-1",
            status="SUCCEEDED_WITH_SIDE_EFFECTS",  # type: ignore[arg-type]
            stdout="",
            stderr="",
            result_json=None,
            output_sha256=None,
            duration_ms=1,
            worker_id="worker-1",
            contract_version=COMPUTATION_CONTRACT_VERSION,
        )
