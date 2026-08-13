from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from aaos.doctor.commands import CommandInspector
from aaos.doctor.models import (
    CommandOutput,
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    GpuObservation,
    NvidiaObservation,
)

_INTEGER_PATTERN = re.compile(r"[0-9]+")


@dataclass(frozen=True, slots=True)
class CommandNvidiaInspector:
    command_inspector: CommandInspector

    def inspect(self) -> NvidiaObservation:
        return parse_nvidia_output(self.command_inspector.inspect_nvidia_output())


def parse_nvidia_output(output: CommandOutput) -> NvidiaObservation:
    if output.code is not None:
        return NvidiaObservation((), output.code)
    if output.text is None:
        return NvidiaObservation((), DiagnosticCode.MALFORMED_OUTPUT)

    try:
        rows = tuple(csv.reader(io.StringIO(output.text, newline="")))
    except csv.Error:
        return NvidiaObservation((), DiagnosticCode.MALFORMED_OUTPUT)
    if not rows:
        return NvidiaObservation((), DiagnosticCode.MALFORMED_OUTPUT)

    gpus: list[GpuObservation] = []
    for row in rows:
        gpu = _parse_row(row)
        if gpu is None:
            return NvidiaObservation((), DiagnosticCode.MALFORMED_OUTPUT)
        gpus.append(gpu)
    return NvidiaObservation(tuple(gpus))


def nvidia_result(observation: NvidiaObservation) -> DiagnosticResult:
    if observation.code is not None:
        return DiagnosticResult(
            "nvidia",
            DiagnosticStatus.WARN,
            "NVIDIA GPU information unavailable",
            code=observation.code,
        )

    facts: list[DiagnosticFact] = [DiagnosticFact("gpu_count", len(observation.gpus))]
    for index, gpu in enumerate(observation.gpus):
        facts.extend(
            (
                DiagnosticFact(f"gpu_{index}_name", gpu.name),
                DiagnosticFact(f"gpu_{index}_total_vram_mib", gpu.total_vram_mib),
                DiagnosticFact(f"gpu_{index}_driver_version", gpu.driver_version),
            )
        )
        if gpu.used_vram_mib is not None:
            facts.append(DiagnosticFact(f"gpu_{index}_used_vram_mib", gpu.used_vram_mib))
        if gpu.free_vram_mib is not None:
            facts.append(DiagnosticFact(f"gpu_{index}_free_vram_mib", gpu.free_vram_mib))
    return DiagnosticResult(
        "nvidia",
        DiagnosticStatus.PASS,
        "NVIDIA GPU information available",
        facts=tuple(facts),
    )


def _parse_row(row: list[str]) -> GpuObservation | None:
    if len(row) != 5:
        return None
    name, total_vram, driver_version, used_vram, free_vram = (field.strip() for field in row)
    if not name or not driver_version:
        return None

    total = _parse_positive_integer(total_vram)
    used = _parse_positive_integer(used_vram)
    free = _parse_positive_integer(free_vram)
    if total is None or used is None or free is None:
        return None
    if used > total or free > total:
        return None
    return GpuObservation(name, total, driver_version, used, free)


def _parse_positive_integer(value: str) -> int | None:
    if _INTEGER_PATTERN.fullmatch(value) is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None
