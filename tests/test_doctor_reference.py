from __future__ import annotations

import pytest

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticStatus,
    FactObservation,
    GpuObservation,
    NvidiaObservation,
    ReferenceMatch,
    ReferenceObservation,
    SystemObservation,
)
from aaos.doctor.reference import (
    REFERENCE_CPU,
    REFERENCE_GPU,
    REFERENCE_RAM_BYTES,
    REFERENCE_VRAM_MIB,
    LocalReferenceInspector,
    ReferenceInspector,
    compare_reference_profile,
    reference_result,
)


def _system(
    *,
    cpu: FactObservation[str] = FactObservation(f"12th Gen {REFERENCE_CPU}"),
    memory: FactObservation[int] = FactObservation(REFERENCE_RAM_BYTES),
) -> SystemObservation:
    return SystemObservation(
        os_name=FactObservation("Windows"),
        architecture=FactObservation("AMD64"),
        cpu_model=cpu,
        logical_processors=FactObservation(20),
        total_memory_bytes=memory,
    )


def _nvidia(
    *gpus: GpuObservation,
    code: DiagnosticCode | None = None,
) -> NvidiaObservation:
    return NvidiaObservation(gpus, code)


_REFERENCE_GPU = GpuObservation(REFERENCE_GPU, REFERENCE_VRAM_MIB, "610.62")


def test_reference_profile_matches_approved_observation_and_passes() -> None:
    observation = compare_reference_profile(_system(), _nvidia(_REFERENCE_GPU))
    result = reference_result(observation)

    assert observation.match is ReferenceMatch.MATCH
    assert result.status is DiagnosticStatus.PASS
    assert result.code is None
    assert result.check_id == "reference_profile"


@pytest.mark.parametrize(
    ("system", "nvidia"),
    [
        (_system(cpu=FactObservation("Intel Core i9-13900H")), _nvidia(_REFERENCE_GPU)),
        (_system(memory=FactObservation(8_589_934_592)), _nvidia(_REFERENCE_GPU)),
        (
            _system(),
            _nvidia(GpuObservation("NVIDIA RTX 4060 Laptop GPU", REFERENCE_VRAM_MIB, "610.62")),
        ),
        (_system(), _nvidia(GpuObservation(REFERENCE_GPU, REFERENCE_VRAM_MIB + 1, "610.62"))),
    ],
)
def test_reference_profile_mismatch_is_warning_only(
    system: SystemObservation, nvidia: NvidiaObservation
) -> None:
    observation = compare_reference_profile(system, nvidia)
    result = reference_result(observation)

    assert observation.match is ReferenceMatch.MISMATCH
    assert result.status is DiagnosticStatus.WARN
    assert result.code is DiagnosticCode.REFERENCE_MISMATCH


@pytest.mark.parametrize(
    ("system", "nvidia"),
    [
        (
            _system(cpu=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM)),
            _nvidia(_REFERENCE_GPU),
        ),
        (
            _system(memory=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM)),
            _nvidia(_REFERENCE_GPU),
        ),
        (_system(), _nvidia(code=DiagnosticCode.MISSING_COMMAND)),
    ],
)
def test_reference_profile_unavailable_input_is_unknown_warning(
    system: SystemObservation, nvidia: NvidiaObservation
) -> None:
    observation = compare_reference_profile(system, nvidia)
    result = reference_result(observation)

    assert observation.match is ReferenceMatch.UNKNOWN
    assert result.status is DiagnosticStatus.WARN
    assert result.code is DiagnosticCode.REFERENCE_UNKNOWN


def test_reference_profile_accepts_one_matching_gpu_on_multi_gpu_machine() -> None:
    observation = compare_reference_profile(
        _system(),
        _nvidia(
            GpuObservation("NVIDIA A100", 81920, "535.216.01"),
            _REFERENCE_GPU,
        ),
    )

    assert observation.match is ReferenceMatch.MATCH
    assert reference_result(observation).status is DiagnosticStatus.PASS


def test_local_reference_inspector_delegates_to_comparison_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = compare_reference_profile(_system(), _nvidia(_REFERENCE_GPU))
    received: list[tuple[SystemObservation, NvidiaObservation]] = []

    def compare(system: SystemObservation, nvidia: NvidiaObservation) -> ReferenceObservation:
        received.append((system, nvidia))
        return expected

    monkeypatch.setattr("aaos.doctor.reference.compare_reference_profile", compare)
    inspector = LocalReferenceInspector()

    assert isinstance(inspector, ReferenceInspector)
    assert inspector.compare(_system(), _nvidia(_REFERENCE_GPU)) is expected
    assert received == [(_system(), _nvidia(_REFERENCE_GPU))]


def test_reference_facts_are_normalized_comparison_values_not_raw_command_output() -> None:
    observation = compare_reference_profile(
        _system(cpu=FactObservation(f"  12th Gen {REFERENCE_CPU}  ")),
        _nvidia(_REFERENCE_GPU),
    )

    fact_values = [str(fact.value) for fact in observation.facts]

    assert "12th Gen" not in " ".join(fact_values)
    assert "Intel(R)" not in " ".join(fact_values)
    assert all("610.62" not in value for value in fact_values)
    assert all("raw" not in fact.name for fact in observation.facts)
