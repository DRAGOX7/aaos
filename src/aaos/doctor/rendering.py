"""Deterministic, safe console rendering for doctor reports."""

from __future__ import annotations

import re

from aaos.doctor.models import DiagnosticFact, DiagnosticResult, DoctorReport

_ALLOWED_FACT_NAMES = frozenset(
    {
        "python_version",
        "pointer_width_bits",
        "os_name",
        "architecture",
        "cpu_model",
        "logical_processors",
        "total_memory_bytes",
        "uv_version",
        "gpu_count",
        "cpu_observed_normalized",
        "cpu_reference_normalized",
        "ram_observed_bytes",
        "ram_reference_bytes",
        "gpu_observed_normalized",
        "gpu_reference_normalized",
        "vram_observed_mib",
        "vram_reference_mib",
        "configured_model",
        "loaded_context_length",
        "required_context_length",
    }
)
_GPU_FACT_NAME = re.compile(
    r"gpu_[0-9]+_(?:name|total_vram_mib|driver_version|used_vram_mib|free_vram_mib)"
)


def render_report(report: DoctorReport) -> str:
    """Render a typed doctor report without raw inspector observations."""
    lines = [f"AAOS doctor: {report.status}"]
    lines.extend(_render_result(result) for result in report.results)
    return "\n".join(lines)


def _render_result(result: DiagnosticResult) -> str:
    line = f"[{result.status}] {result.check_id}: {result.summary}"
    facts = tuple(fact for fact in result.facts if _is_allowed_fact(fact))
    if facts:
        line += f" ({_render_facts(facts)})"
    return line


def _render_facts(facts: tuple[DiagnosticFact, ...]) -> str:
    return ", ".join(f"{fact.name}={_render_fact_value(fact.value)}" for fact in facts)


def _render_fact_value(value: str | int | bool) -> str:
    if not isinstance(value, str):
        return str(value)
    return "".join(
        character if character.isprintable() else f"\\u{ord(character):04X}" for character in value
    )


def _is_allowed_fact(fact: DiagnosticFact) -> bool:
    return fact.name in _ALLOWED_FACT_NAMES or _GPU_FACT_NAME.fullmatch(fact.name) is not None
