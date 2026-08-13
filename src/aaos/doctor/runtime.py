from __future__ import annotations

import struct
import sys
from typing import Protocol, runtime_checkable

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    RuntimeObservation,
)


@runtime_checkable
class RuntimeInspector(Protocol):
    def inspect(self) -> RuntimeObservation: ...


class LocalRuntimeInspector:
    def inspect(self) -> RuntimeObservation:
        return inspect_runtime()


def _read_python_version() -> tuple[int, int]:
    return sys.version_info[:2]


def _read_pointer_width_bits() -> int:
    return struct.calcsize("P") * 8


def inspect_runtime() -> RuntimeObservation:
    python_major, python_minor = _read_python_version()
    return RuntimeObservation(python_major, python_minor, _read_pointer_width_bits())


def runtime_results(
    observation: RuntimeObservation,
) -> tuple[DiagnosticResult, DiagnosticResult]:
    python_version = f"{observation.python_major}.{observation.python_minor}"
    version_fact = DiagnosticFact("python_version", python_version)
    width_fact = DiagnosticFact("pointer_width_bits", observation.pointer_width_bits)

    if (observation.python_major, observation.python_minor) == (3, 11):
        runtime_result = DiagnosticResult(
            "python_runtime",
            DiagnosticStatus.PASS,
            f"Python {python_version} is supported",
            facts=(version_fact,),
        )
    else:
        runtime_result = DiagnosticResult(
            "python_runtime",
            DiagnosticStatus.FAIL,
            f"Python {python_version} is unsupported; Python 3.11 is required",
            facts=(version_fact,),
            code=DiagnosticCode.UNSUPPORTED_RUNTIME,
        )

    if observation.pointer_width_bits == 64:
        width_result = DiagnosticResult(
            "python_width",
            DiagnosticStatus.PASS,
            "64-bit Python runtime",
            facts=(width_fact,),
        )
    else:
        width_result = DiagnosticResult(
            "python_width",
            DiagnosticStatus.FAIL,
            f"{observation.pointer_width_bits}-bit Python runtime; 64-bit is required",
            facts=(width_fact,),
            code=DiagnosticCode.NON_64_BIT_RUNTIME,
        )

    return runtime_result, width_result
