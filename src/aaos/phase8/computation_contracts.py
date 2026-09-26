"""Closed immutable contracts for the Phase 8 computation boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeAlias

COMPUTATION_CONTRACT_VERSION = "phase8-computation-v1"
MAX_CODE_CHARACTERS = 64_000
MAX_INPUTS = 16
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_WALL_TIME_SECONDS = 60
MAX_CPU_TIME_SECONDS = 60
MAX_MEMORY_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_PROCESSES = 32
MAX_RESULT_TEXT_CHARACTERS = 64_000
MAX_DURATION_MS = 60 * 60 * 1000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ComputationMediaType: TypeAlias = Literal["text/plain", "application/json", "image/png"]


class ComputationStatus(StrEnum):
    """Closed lifecycle states returned by one worker attempt."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a safe identifier")
    return value


def _bounded_int(value: object, field: str, maximum: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class ComputationLimits:
    wall_time_seconds: int
    cpu_time_seconds: int
    memory_bytes: int
    output_bytes: int
    max_processes: int
    max_input_bytes: int

    def __post_init__(self) -> None:
        _bounded_int(self.wall_time_seconds, "wall_time_seconds", MAX_WALL_TIME_SECONDS)
        _bounded_int(self.cpu_time_seconds, "cpu_time_seconds", MAX_CPU_TIME_SECONDS)
        _bounded_int(self.memory_bytes, "memory_bytes", MAX_MEMORY_BYTES)
        _bounded_int(self.output_bytes, "output_bytes", MAX_OUTPUT_BYTES)
        _bounded_int(self.max_processes, "max_processes", MAX_PROCESSES)
        _bounded_int(self.max_input_bytes, "max_input_bytes", MAX_INPUT_BYTES)


@dataclass(frozen=True, slots=True)
class ComputationInput:
    input_id: str
    media_type: ComputationMediaType
    content_sha256: str
    bounded_bytes: bytes

    def __post_init__(self) -> None:
        _safe_identifier(self.input_id, "input_id")
        if self.media_type not in {"text/plain", "application/json", "image/png"}:
            raise ValueError("media_type is unsupported")
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.bounded_bytes, bytes):
            raise TypeError("bounded_bytes must be bytes")
        if not self.bounded_bytes or len(self.bounded_bytes) > MAX_INPUT_BYTES:
            raise ValueError("bounded_bytes exceeds the Phase 8 input bound")
        if hashlib.sha256(self.bounded_bytes).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match bounded_bytes")


@dataclass(frozen=True, slots=True)
class ComputationRequest:
    request_id: str
    code: str
    inputs: tuple[ComputationInput, ...]
    limits: ComputationLimits
    contract_version: str

    def __post_init__(self) -> None:
        _safe_identifier(self.request_id, "request_id")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must contain text")
        if len(self.code) > MAX_CODE_CHARACTERS:
            raise ValueError("code exceeds the Phase 8 bound")
        if not isinstance(self.inputs, tuple):
            raise TypeError("inputs must be a tuple")
        if len(self.inputs) > MAX_INPUTS:
            raise ValueError("too many inputs")
        if any(not isinstance(item, ComputationInput) for item in self.inputs):
            raise TypeError("inputs must contain ComputationInput values")
        input_ids = tuple(item.input_id for item in self.inputs)
        if len(set(input_ids)) != len(input_ids):
            raise ValueError("input_id values must be unique")
        if sum(len(item.bounded_bytes) for item in self.inputs) > self.limits.max_input_bytes:
            raise ValueError("inputs exceed max_input_bytes")
        if self.contract_version != COMPUTATION_CONTRACT_VERSION:
            raise ValueError("contract_version is unsupported")


@dataclass(frozen=True, slots=True)
class ComputationResult:
    request_id: str
    status: ComputationStatus
    stdout: str
    stderr: str
    result_json: str | None
    output_sha256: str | None
    duration_ms: int
    worker_id: str
    contract_version: str

    def __post_init__(self) -> None:
        _safe_identifier(self.request_id, "request_id")
        if isinstance(self.status, str) and not isinstance(self.status, ComputationStatus):
            try:
                object.__setattr__(self, "status", ComputationStatus(self.status))
            except ValueError as exc:
                raise ValueError("status is unsupported") from exc
        if not isinstance(self.status, ComputationStatus):
            raise TypeError("status must be a ComputationStatus")
        for field_name, value in (("stdout", self.stdout), ("stderr", self.stderr)):
            if not isinstance(value, str) or len(value) > MAX_RESULT_TEXT_CHARACTERS:
                raise ValueError(f"{field_name} exceeds the Phase 8 result bound")
        if self.result_json is not None and (
            not isinstance(self.result_json, str)
            or len(self.result_json) > MAX_RESULT_TEXT_CHARACTERS
        ):
            raise ValueError("result_json exceeds the Phase 8 result bound")
        if self.output_sha256 is not None and not _SHA256.fullmatch(self.output_sha256):
            raise ValueError("output_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int):
            raise TypeError("duration_ms must be an integer")
        if self.duration_ms < 0 or self.duration_ms > MAX_DURATION_MS:
            raise ValueError("duration_ms exceeds the Phase 8 result bound")
        _safe_identifier(self.worker_id, "worker_id")
        if self.contract_version != COMPUTATION_CONTRACT_VERSION:
            raise ValueError("contract_version is unsupported")


__all__ = [
    "COMPUTATION_CONTRACT_VERSION",
    "ComputationInput",
    "ComputationLimits",
    "ComputationMediaType",
    "ComputationRequest",
    "ComputationResult",
    "ComputationStatus",
    "MAX_CODE_CHARACTERS",
    "MAX_INPUT_BYTES",
    "MAX_INPUTS",
    "MAX_MEMORY_BYTES",
    "MAX_OUTPUT_BYTES",
]
