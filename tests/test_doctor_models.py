from __future__ import annotations

from typing import Any, cast

import pytest

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    DoctorReport,
    FactObservation,
    GpuObservation,
    NvidiaObservation,
    ReferenceMatch,
    ReferenceObservation,
    RuntimeObservation,
    SystemObservation,
)


def test_report_status_and_exit_code_follow_failure_precedence() -> None:
    warning = DiagnosticResult(
        "uv", DiagnosticStatus.WARN, "uv unavailable", code=DiagnosticCode.MISSING_COMMAND
    )
    failure = DiagnosticResult(
        "python",
        DiagnosticStatus.FAIL,
        "unsupported runtime",
        code=DiagnosticCode.UNSUPPORTED_RUNTIME,
    )

    report = DoctorReport((warning, failure))

    assert report.status is DiagnosticStatus.FAIL
    assert report.exit_code == 1


def test_warning_report_exits_zero() -> None:
    report = DoctorReport(
        (
            DiagnosticResult(
                "gpu", DiagnosticStatus.WARN, "unknown", code=DiagnosticCode.COMMAND_FAILED
            ),
        )
    )

    assert report.status is DiagnosticStatus.WARN
    assert report.exit_code == 0


def test_all_pass_report_exits_zero() -> None:
    report = DoctorReport((DiagnosticResult("python", DiagnosticStatus.PASS, "ok"),))

    assert report.status is DiagnosticStatus.PASS
    assert report.exit_code == 0


def test_report_rejects_empty_and_duplicate_check_ids() -> None:
    first = DiagnosticResult("python", DiagnosticStatus.PASS, "ok")
    duplicate = DiagnosticResult("python", DiagnosticStatus.PASS, "also ok")

    with pytest.raises(ValueError, match="at least one result"):
        DoctorReport(())
    with pytest.raises(ValueError, match="unique check_id"):
        DoctorReport((first, duplicate))


def test_result_and_fact_reject_blank_required_text() -> None:
    with pytest.raises(ValueError, match="name"):
        DiagnosticFact(" ", "available")
    with pytest.raises(ValueError, match="value"):
        DiagnosticFact("python", " ")
    with pytest.raises(ValueError, match="check_id"):
        DiagnosticResult(" ", DiagnosticStatus.PASS, "ok")
    with pytest.raises(ValueError, match="summary"):
        DiagnosticResult("python", DiagnosticStatus.PASS, " ")


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (DiagnosticStatus.PASS, DiagnosticCode.COMMAND_FAILED),
        (DiagnosticStatus.WARN, None),
        (DiagnosticStatus.FAIL, None),
    ],
)
def test_result_requires_code_consistent_with_status(
    status: DiagnosticStatus, code: DiagnosticCode | None
) -> None:
    with pytest.raises(ValueError, match="code"):
        DiagnosticResult("check", status, "summary", code=code)


def test_fact_observation_requires_exactly_one_of_value_or_code() -> None:
    with pytest.raises(ValueError, match="value"):
        FactObservation[str](None)
    with pytest.raises(ValueError, match="code"):
        FactObservation("Linux", DiagnosticCode.COMMAND_FAILED)


def test_runtime_and_system_reject_invalid_numeric_resources() -> None:
    unavailable_text = FactObservation[str](None, DiagnosticCode.COMMAND_FAILED)
    unavailable_number = FactObservation[int](None, DiagnosticCode.COMMAND_FAILED)

    with pytest.raises(ValueError, match="python_major"):
        RuntimeObservation(-1, 11, 64)
    with pytest.raises(ValueError, match="pointer_width_bits"):
        RuntimeObservation(3, 11, -1)
    with pytest.raises(ValueError, match="logical_processors"):
        SystemObservation(
            unavailable_text,
            unavailable_text,
            unavailable_text,
            FactObservation(0),
            unavailable_number,
        )
    with pytest.raises(ValueError, match="total_memory_bytes"):
        SystemObservation(
            unavailable_text,
            unavailable_text,
            unavailable_text,
            unavailable_number,
            FactObservation(-1),
        )


def test_gpu_and_nvidia_observations_reject_invalid_resource_states() -> None:
    gpu = GpuObservation("RTX 4050", 6144, "576.80")
    idle_gpu = GpuObservation(
        "RTX 4050",
        6144,
        "576.80",
        used_vram_mib=0,
        free_vram_mib=0,
    )

    assert idle_gpu.used_vram_mib == 0
    assert idle_gpu.free_vram_mib == 0

    with pytest.raises(ValueError, match="total_vram_mib"):
        GpuObservation("RTX 4050", 0, "576.80")
    with pytest.raises(ValueError, match="used_vram_mib"):
        GpuObservation("RTX 4050", 6144, "576.80", used_vram_mib=-1)
    with pytest.raises(ValueError, match="free_vram_mib"):
        GpuObservation("RTX 4050", 6144, "576.80", free_vram_mib=-1)
    with pytest.raises(ValueError, match="at least one GPU"):
        NvidiaObservation(())
    with pytest.raises(ValueError, match="no GPUs"):
        NvidiaObservation((gpu,), DiagnosticCode.COMMAND_FAILED)


def test_tuple_fields_reject_mutable_lists() -> None:
    result = DiagnosticResult("python", DiagnosticStatus.PASS, "ok")
    fact = DiagnosticFact("python", "3.11")
    gpu = GpuObservation("RTX 4050", 6144, "576.80")

    with pytest.raises(TypeError, match="results must be a tuple"):
        DoctorReport(cast(Any, [result]))
    with pytest.raises(TypeError, match="facts must be a tuple"):
        DiagnosticResult("python", DiagnosticStatus.PASS, "ok", cast(Any, [fact]))
    with pytest.raises(TypeError, match="gpus must be a tuple"):
        NvidiaObservation(cast(Any, [gpu]))
    with pytest.raises(TypeError, match="facts must be a tuple"):
        ReferenceObservation(ReferenceMatch.MATCH, cast(Any, [fact]))


def test_tuple_fields_reject_wrong_member_types() -> None:
    with pytest.raises(TypeError, match="DiagnosticResult"):
        DoctorReport(cast(Any, ("python",)))
    with pytest.raises(TypeError, match="DiagnosticFact"):
        DiagnosticResult("python", DiagnosticStatus.PASS, "ok", cast(Any, ("fact",)))
    with pytest.raises(TypeError, match="GpuObservation"):
        NvidiaObservation(cast(Any, ("gpu",)))
    with pytest.raises(TypeError, match="DiagnosticFact"):
        ReferenceObservation(ReferenceMatch.MATCH, cast(Any, ("fact",)))


@pytest.mark.parametrize("field_name", ["python_major", "python_minor", "pointer_width_bits"])
def test_runtime_numeric_fields_reject_bool(field_name: str) -> None:
    values: dict[str, int] = {
        "python_major": 3,
        "python_minor": 11,
        "pointer_width_bits": 64,
    }
    values[field_name] = True

    with pytest.raises(TypeError, match=field_name):
        RuntimeObservation(**values)


@pytest.mark.parametrize("field_name", ["logical_processors", "total_memory_bytes"])
def test_system_numeric_facts_reject_bool(field_name: str) -> None:
    unavailable_text = FactObservation[str](None, DiagnosticCode.COMMAND_FAILED)
    values = {
        "os_name": unavailable_text,
        "architecture": unavailable_text,
        "cpu_model": unavailable_text,
        "logical_processors": FactObservation(12),
        "total_memory_bytes": FactObservation(16_000_000_000),
    }
    values[field_name] = cast(Any, FactObservation(True))

    with pytest.raises(TypeError, match=field_name):
        SystemObservation(**cast(Any, values))


@pytest.mark.parametrize("field_name", ["total_vram_mib", "used_vram_mib", "free_vram_mib"])
def test_gpu_numeric_fields_reject_bool(field_name: str) -> None:
    values: dict[str, Any] = {
        "name": "RTX 4050",
        "total_vram_mib": 6144,
        "driver_version": "576.80",
        "used_vram_mib": 1024,
        "free_vram_mib": 5120,
    }
    values[field_name] = True

    with pytest.raises(TypeError, match=field_name):
        GpuObservation(**values)
