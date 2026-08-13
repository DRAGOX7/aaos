from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar


class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class DiagnosticCode(StrEnum):
    UNSUPPORTED_RUNTIME = "unsupported_runtime"
    NON_64_BIT_RUNTIME = "non_64_bit_runtime"
    MISSING_COMMAND = "missing_command"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MALFORMED_OUTPUT = "malformed_output"
    OVERSIZED_OUTPUT = "oversized_output"
    COMMAND_FAILED = "command_failed"
    NOT_EVALUATED = "not_evaluated"
    CONNECTION_FAILED = "connection_failed"
    PROTOCOL_FAILED = "protocol_failed"
    BACKEND_FAILED = "backend_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    INFERENCE_FAILED = "inference_failed"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    REFERENCE_MISMATCH = "reference_mismatch"
    REFERENCE_UNKNOWN = "reference_unknown"


FactValue: TypeAlias = str | int | bool


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-blank")


@dataclass(frozen=True, slots=True)
class DiagnosticFact:
    name: str
    value: FactValue

    def __post_init__(self) -> None:
        _require_non_blank(self.name, "name")
        if isinstance(self.value, str):
            _require_non_blank(self.value, "value")


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    check_id: str
    status: DiagnosticStatus
    summary: str
    facts: tuple[DiagnosticFact, ...] = ()
    code: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.check_id, "check_id")
        _require_non_blank(self.summary, "summary")
        if self.status is DiagnosticStatus.PASS and self.code is not None:
            raise ValueError("PASS results must not have a code")
        if self.status is not DiagnosticStatus.PASS and self.code is None:
            raise ValueError("WARN and FAIL results must have a code")


@dataclass(frozen=True, slots=True)
class DoctorReport:
    results: tuple[DiagnosticResult, ...]

    def __post_init__(self) -> None:
        if not self.results:
            raise ValueError("DoctorReport must contain at least one result")
        check_ids = tuple(result.check_id for result in self.results)
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("DoctorReport results must have unique check_id values")

    @property
    def status(self) -> DiagnosticStatus:
        if any(result.status is DiagnosticStatus.FAIL for result in self.results):
            return DiagnosticStatus.FAIL
        if any(result.status is DiagnosticStatus.WARN for result in self.results):
            return DiagnosticStatus.WARN
        return DiagnosticStatus.PASS

    @property
    def exit_code(self) -> int:
        return 1 if self.status is DiagnosticStatus.FAIL else 0


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FactObservation(Generic[T]):
    value: T | None
    code: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.code is None):
            raise ValueError("value must be present exactly when code is absent")


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    python_major: int
    python_minor: int
    pointer_width_bits: int

    def __post_init__(self) -> None:
        if self.python_major < 0:
            raise ValueError("python_major must be non-negative")
        if self.python_minor < 0:
            raise ValueError("python_minor must be non-negative")
        if self.pointer_width_bits < 0:
            raise ValueError("pointer_width_bits must be non-negative")


@dataclass(frozen=True, slots=True)
class SystemObservation:
    os_name: FactObservation[str]
    architecture: FactObservation[str]
    cpu_model: FactObservation[str]
    logical_processors: FactObservation[int]
    total_memory_bytes: FactObservation[int]

    def __post_init__(self) -> None:
        self._require_positive_when_present(self.logical_processors, "logical_processors")
        self._require_positive_when_present(self.total_memory_bytes, "total_memory_bytes")

    @staticmethod
    def _require_positive_when_present(observation: FactObservation[int], field_name: str) -> None:
        if observation.value is not None and observation.value <= 0:
            raise ValueError(f"{field_name} must be positive when present")


@dataclass(frozen=True, slots=True)
class CommandOutput:
    text: str | None
    code: DiagnosticCode | None = None


@dataclass(frozen=True, slots=True)
class UvObservation:
    version: str | None
    code: DiagnosticCode | None = None


@dataclass(frozen=True, slots=True)
class GpuObservation:
    name: str
    total_vram_mib: int
    driver_version: str
    used_vram_mib: int | None = None
    free_vram_mib: int | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.name, "name")
        _require_non_blank(self.driver_version, "driver_version")
        if self.total_vram_mib <= 0:
            raise ValueError("total_vram_mib must be positive")
        if self.used_vram_mib is not None and self.used_vram_mib <= 0:
            raise ValueError("used_vram_mib must be positive when present")
        if self.free_vram_mib is not None and self.free_vram_mib <= 0:
            raise ValueError("free_vram_mib must be positive when present")


@dataclass(frozen=True, slots=True)
class NvidiaObservation:
    gpus: tuple[GpuObservation, ...]
    code: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        if self.code is None and not self.gpus:
            raise ValueError("successful NvidiaObservation must contain at least one GPU")
        if self.code is not None and self.gpus:
            raise ValueError("failed NvidiaObservation must contain no GPUs")


class ReferenceMatch(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReferenceObservation:
    match: ReferenceMatch
    facts: tuple[DiagnosticFact, ...]
