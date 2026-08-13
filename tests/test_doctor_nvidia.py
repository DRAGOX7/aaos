from __future__ import annotations

from dataclasses import dataclass

import pytest

from aaos.doctor.commands import CommandInspector
from aaos.doctor.models import (
    CommandOutput,
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    GpuObservation,
    NvidiaObservation,
    UvObservation,
)
from aaos.doctor.nvidia import CommandNvidiaInspector, nvidia_result, parse_nvidia_output

_RTX_4050_ROW = "NVIDIA GeForce RTX 4050 Laptop GPU, 6141, 610.62, 4781, 1360"


@dataclass
class StubCommandInspector:
    output: CommandOutput
    nvidia_calls: int = 0

    def inspect_uv(self) -> UvObservation:
        return UvObservation(version="0.1.0")

    def inspect_nvidia_output(self) -> CommandOutput:
        self.nvidia_calls += 1
        return self.output


def test_parse_nvidia_output_returns_one_validated_gpu() -> None:
    observation = parse_nvidia_output(CommandOutput(text=_RTX_4050_ROW))

    assert observation == NvidiaObservation(
        (
            GpuObservation(
                "NVIDIA GeForce RTX 4050 Laptop GPU",
                6141,
                "610.62",
                used_vram_mib=4781,
                free_vram_mib=1360,
            ),
        )
    )


def test_parse_nvidia_output_preserves_backend_gpu_order() -> None:
    second_row = "NVIDIA A100, 81920, 535.216.01, 1, 81919"

    observation = parse_nvidia_output(CommandOutput(text=f"{_RTX_4050_ROW}\n{second_row}"))

    assert observation.gpus == (
        GpuObservation("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, "610.62", 4781, 1360),
        GpuObservation("NVIDIA A100", 81920, "535.216.01", 1, 81919),
    )


@pytest.mark.parametrize("text", ["", " \r\n\t "])
def test_parse_nvidia_output_rejects_blank_rows(text: str) -> None:
    assert parse_nvidia_output(CommandOutput(text=text)) == NvidiaObservation(
        (), DiagnosticCode.MALFORMED_OUTPUT
    )


@pytest.mark.parametrize(
    "text",
    [
        "NVIDIA RTX, 6141, 610.62, 4781",
        ", 6141, 610.62, 4781, 1360",
        "NVIDIA RTX, 6141, , 4781, 1360",
        "NVIDIA RTX, invalid, 610.62, 4781, 1360",
        "NVIDIA RTX, -1, 610.62, 4781, 1360",
        "NVIDIA RTX, 6141, 610.62, -1, 1360",
        "NVIDIA RTX, 6141, 610.62, 4781, -1",
        "NVIDIA RTX, 6141, 610.62, 6142, 1",
        "NVIDIA RTX, 6141, 610.62, 1, 6142",
    ],
)
def test_parse_nvidia_output_rejects_invalid_csv_values(text: str) -> None:
    assert parse_nvidia_output(CommandOutput(text=text)) == NvidiaObservation(
        (), DiagnosticCode.MALFORMED_OUTPUT
    )


def test_parse_nvidia_output_does_not_apply_hardware_thresholds() -> None:
    observation = parse_nvidia_output(CommandOutput(text="NVIDIA T4, 16, 1.0, 1, 15"))

    assert observation == NvidiaObservation((GpuObservation("NVIDIA T4", 16, "1.0", 1, 15),))


@pytest.mark.parametrize(
    "code",
    [
        DiagnosticCode.MISSING_COMMAND,
        DiagnosticCode.PERMISSION_DENIED,
        DiagnosticCode.TIMEOUT,
        DiagnosticCode.COMMAND_FAILED,
        DiagnosticCode.OVERSIZED_OUTPUT,
    ],
)
def test_parse_nvidia_output_propagates_command_failure(code: DiagnosticCode) -> None:
    assert parse_nvidia_output(CommandOutput(text=None, code=code)) == NvidiaObservation((), code)


def test_command_nvidia_inspector_calls_command_inspector_once() -> None:
    command_inspector = StubCommandInspector(CommandOutput(text=_RTX_4050_ROW))
    inspector = CommandNvidiaInspector(command_inspector)

    observation = inspector.inspect()

    assert isinstance(command_inspector, CommandInspector)
    assert command_inspector.nvidia_calls == 1
    assert observation.gpus[0].name == "NVIDIA GeForce RTX 4050 Laptop GPU"


def test_nvidia_result_reports_validated_gpu_facts() -> None:
    result = nvidia_result(NvidiaObservation((GpuObservation("NVIDIA T4", 16, "1.0", 1, 15),)))

    assert result == DiagnosticResult(
        "nvidia",
        DiagnosticStatus.PASS,
        "NVIDIA GPU information available",
        facts=(
            DiagnosticFact("gpu_count", 1),
            DiagnosticFact("gpu_0_name", "NVIDIA T4"),
            DiagnosticFact("gpu_0_total_vram_mib", 16),
            DiagnosticFact("gpu_0_driver_version", "1.0"),
            DiagnosticFact("gpu_0_used_vram_mib", 1),
            DiagnosticFact("gpu_0_free_vram_mib", 15),
        ),
    )


@pytest.mark.parametrize(
    "observation",
    [
        NvidiaObservation((), DiagnosticCode.MISSING_COMMAND),
        NvidiaObservation((), DiagnosticCode.PERMISSION_DENIED),
        NvidiaObservation((), DiagnosticCode.TIMEOUT),
        NvidiaObservation((), DiagnosticCode.COMMAND_FAILED),
        NvidiaObservation((), DiagnosticCode.OVERSIZED_OUTPUT),
        NvidiaObservation((), DiagnosticCode.MALFORMED_OUTPUT),
    ],
)
def test_nvidia_result_warns_for_unavailable_optional_data(observation: NvidiaObservation) -> None:
    result = nvidia_result(observation)

    assert result == DiagnosticResult(
        "nvidia",
        DiagnosticStatus.WARN,
        "NVIDIA GPU information unavailable",
        code=observation.code,
    )


def test_nvidia_result_never_exposes_raw_command_text_or_errors() -> None:
    sentinel_csv = "SENTINEL raw csv, 6141, 610.62, 4781, 1360, unexpected"
    sentinel_error = "SENTINEL stderr exception detail"
    malformed = parse_nvidia_output(CommandOutput(text=sentinel_csv))
    failure = parse_nvidia_output(
        CommandOutput(text=sentinel_error, code=DiagnosticCode.COMMAND_FAILED)
    )

    for result in (nvidia_result(malformed), nvidia_result(failure)):
        assert "SENTINEL" not in result.summary
        assert all("SENTINEL" not in str(fact.value) for fact in result.facts)
