"""Bounded, versioned JSON protocol for one Phase 8 worker attempt."""

from __future__ import annotations

import base64
import json
from typing import Any

from .computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationRequest,
    ComputationResult,
)

WORKER_PROTOCOL_VERSION = "phase8-worker-v1"
MAX_PROTOCOL_PAYLOAD_BYTES = 4 * 1024 * 1024
_REQUEST_FIELDS = {
    "protocol_version",
    "request_id",
    "code",
    "inputs",
    "limits",
    "contract_version",
}
_INPUT_FIELDS = {"input_id", "media_type", "content_sha256", "bounded_bytes_base64"}
_LIMIT_FIELDS = {
    "wall_time_seconds",
    "cpu_time_seconds",
    "memory_bytes",
    "output_bytes",
    "max_processes",
    "max_input_bytes",
}
_RESULT_FIELDS = {
    "protocol_version",
    "request_id",
    "status",
    "stdout",
    "stderr",
    "result_json",
    "output_sha256",
    "duration_ms",
    "worker_id",
    "contract_version",
}


class WorkerProtocolError(ValueError):
    """Safe closed protocol failure without raw worker details."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " ").lower())


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise WorkerProtocolError("INVALID_JSON") from exc


def _check_object(value: object, expected: set[str], kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkerProtocolError(f"INVALID_{kind.upper()}")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise WorkerProtocolError(f"unknown field in {kind}")
    if missing:
        raise WorkerProtocolError(f"missing field in {kind}")
    return value


def _encode_request_payload(request: ComputationRequest) -> dict[str, object]:
    return {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "request_id": request.request_id,
        "code": request.code,
        "inputs": [
            {
                "input_id": item.input_id,
                "media_type": item.media_type,
                "content_sha256": item.content_sha256,
                "bounded_bytes_base64": base64.b64encode(item.bounded_bytes).decode("ascii"),
            }
            for item in request.inputs
        ],
        "limits": {
            "wall_time_seconds": request.limits.wall_time_seconds,
            "cpu_time_seconds": request.limits.cpu_time_seconds,
            "memory_bytes": request.limits.memory_bytes,
            "output_bytes": request.limits.output_bytes,
            "max_processes": request.limits.max_processes,
            "max_input_bytes": request.limits.max_input_bytes,
        },
        "contract_version": request.contract_version,
    }


def encode_worker_request(request: ComputationRequest) -> bytes:
    """Encode one validated request as deterministic canonical JSON."""

    if not isinstance(request, ComputationRequest):
        raise TypeError("request must be a ComputationRequest")
    encoded = _canonical_json(_encode_request_payload(request))
    if len(encoded) > MAX_PROTOCOL_PAYLOAD_BYTES:
        raise WorkerProtocolError("PAYLOAD_TOO_LARGE")
    return encoded


def decode_worker_result(payload: bytes) -> ComputationResult:
    """Decode and strictly validate one worker result envelope."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) > MAX_PROTOCOL_PAYLOAD_BYTES:
        raise WorkerProtocolError("PAYLOAD_TOO_LARGE")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerProtocolError("INVALID_JSON") from exc
    result = _check_object(value, _RESULT_FIELDS, "result")
    if result["protocol_version"] != WORKER_PROTOCOL_VERSION:
        raise WorkerProtocolError("PROTOCOL_VERSION_MISMATCH")
    if result["contract_version"] != COMPUTATION_CONTRACT_VERSION:
        raise WorkerProtocolError("CONTRACT_VERSION_MISMATCH")
    try:
        return ComputationResult(
            request_id=result["request_id"],
            status=result["status"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            result_json=result["result_json"],
            output_sha256=result["output_sha256"],
            duration_ms=result["duration_ms"],
            worker_id=result["worker_id"],
            contract_version=result["contract_version"],
        )
    except (TypeError, ValueError) as exc:
        raise WorkerProtocolError("INVALID_RESULT") from exc


__all__ = [
    "MAX_PROTOCOL_PAYLOAD_BYTES",
    "WORKER_PROTOCOL_VERSION",
    "WorkerProtocolError",
    "decode_worker_result",
    "encode_worker_request",
]
