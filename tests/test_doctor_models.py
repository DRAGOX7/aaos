from __future__ import annotations

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

    with pytest.raises(ValueError, match="total_vram_mib"):
        GpuObservation("RTX 4050", 0, "576.80")
    with pytest.raises(ValueError, match="used_vram_mib"):
        GpuObservation("RTX 4050", 6144, "576.80", used_vram_mib=0)
    with pytest.raises(ValueError, match="free_vram_mib"):
        GpuObservation("RTX 4050", 6144, "576.80", free_vram_mib=-1)
    with pytest.raises(ValueError, match="at least one GPU"):
        NvidiaObservation(())
    with pytest.raises(ValueError, match="no GPUs"):
        NvidiaObservation((gpu,), DiagnosticCode.COMMAND_FAILED)
