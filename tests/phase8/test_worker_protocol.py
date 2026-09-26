from __future__ import annotations

import json
from hashlib import sha256

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
    ComputationStatus,
)
from aaos.phase8.worker_protocol import (
    WORKER_PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_worker_result,
    encode_worker_request,
)


def _request() -> ComputationRequest:
    content = b"input"
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(ComputationInput("input-1", "text/plain", sha256(content).hexdigest(), content),),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def _result_payload(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "request_id": "request-1",
        "status": ComputationStatus.SUCCEEDED.value,
        "stdout": "2\n",
        "stderr": "",
        "result_json": '{"value":2}',
        "output_sha256": None,
        "duration_ms": 1,
        "worker_id": "worker-1",
        "contract_version": COMPUTATION_CONTRACT_VERSION,
    }
    value.update(overrides)
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def test_request_encoding_is_canonical_and_does_not_use_pickle() -> None:
    encoded = encode_worker_request(_request())
    assert encoded == encode_worker_request(_request())
    decoded = json.loads(encoded)
    assert decoded["protocol_version"] == WORKER_PROTOCOL_VERSION
    assert decoded["request_id"] == "request-1"
    assert decoded["inputs"][0]["bounded_bytes_base64"] == "aW5wdXQ="
    assert b"pickle" not in encoded.lower()


def test_result_decoding_is_strict_and_versioned() -> None:
    result = decode_worker_result(_result_payload())
    assert result.status is ComputationStatus.SUCCEEDED
    with pytest.raises(WorkerProtocolError, match="unknown field"):
        decode_worker_result(_result_payload(untrusted_path="C:/secret"))
    with pytest.raises(WorkerProtocolError, match="version"):
        decode_worker_result(_result_payload(protocol_version="phase8-worker-v0"))


def test_malformed_and_oversized_payloads_fail_closed() -> None:
    with pytest.raises(WorkerProtocolError, match="json"):
        decode_worker_result(b"not-json")
    with pytest.raises(WorkerProtocolError, match="payload"):
        decode_worker_result(b"x" * (4 * 1024 * 1024 + 1))
