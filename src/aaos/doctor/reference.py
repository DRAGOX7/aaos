from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    GpuObservation,
    NvidiaObservation,
    ReferenceMatch,
    ReferenceObservation,
    SystemObservation,
)

REFERENCE_CPU = "Intel(R) Core(TM) i7-12700H"
REFERENCE_RAM_BYTES = 16_788_852_736
REFERENCE_GPU = "NVIDIA GeForce RTX 4050 Laptop GPU"
REFERENCE_VRAM_MIB = 6141
REFERENCE_RAM_TOLERANCE_BYTES = 512 * 1024 * 1024

_TRADEMARK_PUNCTUATION = re.compile(r"\(r\)|\(tm\)|[®™]", re.IGNORECASE)
_PROCESSOR_GENERATION_PREFIX = re.compile(r"\b\d+(?:st|nd|rd|th)\s+gen\s+", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


@runtime_checkable
class ReferenceInspector(Protocol):
    def compare(self) -> ReferenceObservation: ...


@dataclass(frozen=True, slots=True)
class LocalReferenceInspector:
    system: SystemObservation
    nvidia: NvidiaObservation

    def compare(self) -> ReferenceObservation:
        return compare_reference_profile(self.system, self.nvidia)


def compare_reference_profile(
    system: SystemObservation, nvidia: NvidiaObservation
) -> ReferenceObservation:
    """Compare observed hardware with the documented development reference profile.

    This is informational only: it identifies whether the local observation resembles
    the measured reference machine and does not enforce a capability threshold.
    """
    cpu = system.cpu_model.value
    memory = system.total_memory_bytes.value
    if cpu is None or memory is None or nvidia.code is not None:
        return ReferenceObservation(ReferenceMatch.UNKNOWN, _available_facts(system, nvidia))

    gpu = _matching_gpu(nvidia)
    facts = _comparison_facts(cpu, memory, nvidia, gpu)
    if _normalize_cpu(cpu) != _normalize_cpu(REFERENCE_CPU):
        return ReferenceObservation(ReferenceMatch.MISMATCH, facts)
    if abs(memory - REFERENCE_RAM_BYTES) > REFERENCE_RAM_TOLERANCE_BYTES:
        return ReferenceObservation(ReferenceMatch.MISMATCH, facts)
    if gpu is None:
        return ReferenceObservation(ReferenceMatch.MISMATCH, facts)
    return ReferenceObservation(ReferenceMatch.MATCH, facts)


def reference_result(observation: ReferenceObservation) -> DiagnosticResult:
    if observation.match is ReferenceMatch.MATCH:
        return DiagnosticResult(
            "reference_profile",
            DiagnosticStatus.PASS,
            "Hardware matches the documented reference profile",
            facts=observation.facts,
        )
    if observation.match is ReferenceMatch.MISMATCH:
        return DiagnosticResult(
            "reference_profile",
            DiagnosticStatus.WARN,
            "Hardware differs from the documented reference profile",
            facts=observation.facts,
            code=DiagnosticCode.REFERENCE_MISMATCH,
        )
    return DiagnosticResult(
        "reference_profile",
        DiagnosticStatus.WARN,
        "Hardware reference comparison is incomplete",
        facts=observation.facts,
        code=DiagnosticCode.REFERENCE_UNKNOWN,
    )


def _matching_gpu(nvidia: NvidiaObservation) -> GpuObservation | None:
    reference_name = _normalize_gpu(REFERENCE_GPU)
    return next(
        (
            gpu
            for gpu in nvidia.gpus
            if _normalize_gpu(gpu.name) == reference_name
            and gpu.total_vram_mib == REFERENCE_VRAM_MIB
        ),
        None,
    )


def _comparison_facts(
    cpu: str,
    memory: int,
    nvidia: NvidiaObservation,
    matching_gpu: GpuObservation | None,
) -> tuple[DiagnosticFact, ...]:
    gpu = matching_gpu if matching_gpu is not None else nvidia.gpus[0]
    return (
        DiagnosticFact("cpu_observed_normalized", _normalize_cpu(cpu)),
        DiagnosticFact("cpu_reference_normalized", _normalize_cpu(REFERENCE_CPU)),
        DiagnosticFact("ram_observed_bytes", memory),
        DiagnosticFact("ram_reference_bytes", REFERENCE_RAM_BYTES),
        DiagnosticFact("gpu_observed_normalized", _normalize_gpu(gpu.name)),
        DiagnosticFact("gpu_reference_normalized", _normalize_gpu(REFERENCE_GPU)),
        DiagnosticFact("vram_observed_mib", gpu.total_vram_mib),
        DiagnosticFact("vram_reference_mib", REFERENCE_VRAM_MIB),
    )


def _available_facts(
    system: SystemObservation, nvidia: NvidiaObservation
) -> tuple[DiagnosticFact, ...]:
    facts: list[DiagnosticFact] = []
    if system.cpu_model.value is not None:
        facts.extend(
            (
                DiagnosticFact("cpu_observed_normalized", _normalize_cpu(system.cpu_model.value)),
                DiagnosticFact("cpu_reference_normalized", _normalize_cpu(REFERENCE_CPU)),
            )
        )
    if system.total_memory_bytes.value is not None:
        facts.extend(
            (
                DiagnosticFact("ram_observed_bytes", system.total_memory_bytes.value),
                DiagnosticFact("ram_reference_bytes", REFERENCE_RAM_BYTES),
            )
        )
    if nvidia.gpus:
        gpu = nvidia.gpus[0]
        facts.extend(
            (
                DiagnosticFact("gpu_observed_normalized", _normalize_gpu(gpu.name)),
                DiagnosticFact("gpu_reference_normalized", _normalize_gpu(REFERENCE_GPU)),
                DiagnosticFact("vram_observed_mib", gpu.total_vram_mib),
                DiagnosticFact("vram_reference_mib", REFERENCE_VRAM_MIB),
            )
        )
    return tuple(facts)


def _normalize_cpu(value: str) -> str:
    without_generation = _PROCESSOR_GENERATION_PREFIX.sub("", value)
    return _normalize_identity(without_generation)


def _normalize_gpu(value: str) -> str:
    return _normalize_identity(value)


def _normalize_identity(value: str) -> str:
    without_trademarks = _TRADEMARK_PUNCTUATION.sub("", value)
    return _WHITESPACE.sub(" ", without_trademarks).strip().casefold()
