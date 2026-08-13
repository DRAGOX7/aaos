from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import NoReturn

import pytest

from aaos.config import AppSettings
from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticStatus,
    DoctorReport,
    FactObservation,
    GpuObservation,
    NvidiaObservation,
    ReferenceMatch,
    ReferenceObservation,
    RuntimeObservation,
    SystemObservation,
    UvObservation,
)
from aaos.doctor.service import DoctorService
from aaos.inference import (
    ChatRequest,
    ChatResponse,
    InferenceBackendError,
    InferenceConnectionError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelAvailability,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)

EXPECTED_ORDER = (
    "python_runtime",
    "python_width",
    "platform",
    "cpu",
    "memory",
    "uv",
    "nvidia",
    "reference_profile",
    "lm_studio",
    "configured_model",
    "loaded_context",
)

_REFERENCE_SYSTEM = SystemObservation(
    os_name=FactObservation("Windows"),
    architecture=FactObservation("AMD64"),
    cpu_model=FactObservation("12th Gen Intel(R) Core(TM) i7-12700H"),
    logical_processors=FactObservation(20),
    total_memory_bytes=FactObservation(16_788_852_736),
)
_REFERENCE_NVIDIA = NvidiaObservation(
    (
        GpuObservation(
            "NVIDIA GeForce RTX 4050 Laptop GPU",
            6141,
            "555.85",
            100,
            6041,
        ),
    )
)


@dataclass
class FakeRuntimeInspector:
    observation: RuntimeObservation = RuntimeObservation(3, 11, 64)
    calls: list[str] = field(default_factory=list)

    def inspect(self) -> RuntimeObservation:
        self.calls.append("runtime")
        return self.observation


@dataclass
class FakeSystemInspector:
    observation: SystemObservation = _REFERENCE_SYSTEM
    calls: list[str] = field(default_factory=list)
    error: Exception | None = None

    def inspect(self) -> SystemObservation:
        self.calls.append("system")
        if self.error is not None:
            raise self.error
        return self.observation


@dataclass
class FakeCommandInspector:
    observation: UvObservation = UvObservation("0.12.3")
    calls: list[str] = field(default_factory=list)

    def inspect_uv(self) -> UvObservation:
        self.calls.append("uv")
        return self.observation

    def inspect_nvidia_output(self) -> NoReturn:
        raise AssertionError("the service must use the injected NVIDIA inspector")


@dataclass
class FakeNvidiaInspector:
    observation: NvidiaObservation = _REFERENCE_NVIDIA
    calls: list[str] = field(default_factory=list)

    def inspect(self) -> NvidiaObservation:
        self.calls.append("nvidia")
        return self.observation


@dataclass
class FakeReferenceInspector:
    calls: list[str] = field(default_factory=list)

    def compare(self, system: SystemObservation, nvidia: NvidiaObservation) -> ReferenceObservation:
        self.calls.append("reference")
        if system != _REFERENCE_SYSTEM or nvidia != _REFERENCE_NVIDIA:
            return ReferenceObservation(ReferenceMatch.UNKNOWN, ())
        return ReferenceObservation(ReferenceMatch.MATCH, ())


@dataclass
class FakeGateway:
    availability: ModelAvailability | None = None
    error: InferenceError | None = None
    calls: int = 0

    async def check_availability(self) -> ModelAvailability:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.availability is not None
        return self.availability

    async def generate(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("doctor diagnostics must not generate")


@dataclass
class FakeCloser:
    calls: int = 0

    async def close(self) -> None:
        self.calls += 1


def available_gateway() -> FakeGateway:
    return FakeGateway(
        availability=ModelAvailability(
            reachable=True,
            available=True,
            configured_model="untrusted gateway model",
            available_models=("untrusted gateway model",),
            loaded_context_lengths=(8192,),
        )
    )


def make_service(
    *,
    runtime: FakeRuntimeInspector | None = None,
    system: FakeSystemInspector | None = None,
    command: FakeCommandInspector | None = None,
    nvidia: FakeNvidiaInspector | None = None,
    reference: FakeReferenceInspector | None = None,
    gateway: FakeGateway | None = None,
    closer: FakeCloser | None = None,
) -> tuple[DoctorService, FakeGateway, FakeCloser]:
    configured_gateway = gateway or available_gateway()
    configured_closer = closer or FakeCloser()
    service = DoctorService(
        settings=AppSettings(),
        runtime_inspector=runtime or FakeRuntimeInspector(),
        system_inspector=system or FakeSystemInspector(),
        command_inspector=command or FakeCommandInspector(),
        nvidia_inspector=nvidia or FakeNvidiaInspector(),
        reference_inspector=reference or FakeReferenceInspector(),
        model_gateway=configured_gateway,
        close_model_gateway=configured_closer.close,
    )
    return service, configured_gateway, configured_closer


def run(service: DoctorService) -> DoctorReport:
    return asyncio.run(service.run())


def test_all_established_diagnostics_pass_in_the_approved_order() -> None:
    reference = FakeReferenceInspector()
    service, gateway, closer = make_service(reference=reference)

    report = run(service)

    assert tuple(result.check_id for result in report.results) == EXPECTED_ORDER
    assert report.status is DiagnosticStatus.PASS
    assert report.exit_code == 0
    assert gateway.calls == 1
    assert closer.calls == 1
    assert reference.calls == ["reference"]


def test_optional_uv_nvidia_and_reference_warnings_do_not_fail_doctor() -> None:
    service, _, closer = make_service(
        command=FakeCommandInspector(UvObservation(None, DiagnosticCode.MISSING_COMMAND)),
        nvidia=FakeNvidiaInspector(NvidiaObservation((), DiagnosticCode.MISSING_COMMAND)),
    )

    report = run(service)

    assert tuple(result.check_id for result in report.results) == EXPECTED_ORDER
    assert [result.status for result in report.results[5:8]] == [DiagnosticStatus.WARN] * 3
    assert report.status is DiagnosticStatus.WARN
    assert report.exit_code == 0
    assert closer.calls == 1


def test_uv_result_exposes_only_the_typed_version() -> None:
    sentinel = "SENTINEL raw subprocess output"
    service, _, _ = make_service(command=FakeCommandInspector(UvObservation("0.12.3")))

    report = run(service)

    uv_result = report.results[5]
    assert uv_result.check_id == "uv"
    assert uv_result.status is DiagnosticStatus.PASS
    assert uv_result.facts == (DiagnosticFact("uv_version", "0.12.3"),)
    assert sentinel not in f"{uv_result.summary} {uv_result.facts}"


def test_unavailable_optional_system_facts_warn_without_failing() -> None:
    unavailable_text: FactObservation[str] = FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )
    unavailable_integer: FactObservation[int] = FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )
    system = FakeSystemInspector(
        SystemObservation(
            os_name=unavailable_text,
            architecture=unavailable_text,
            cpu_model=unavailable_text,
            logical_processors=unavailable_integer,
            total_memory_bytes=unavailable_integer,
        )
    )
    service, _, _ = make_service(system=system, nvidia=FakeNvidiaInspector(_REFERENCE_NVIDIA))

    report = run(service)

    assert [result.status for result in report.results[2:5]] == [DiagnosticStatus.WARN] * 3
    assert all(result.status is not DiagnosticStatus.FAIL for result in report.results[2:5])
    assert report.status is DiagnosticStatus.WARN
    assert report.exit_code == 0


def test_required_runtime_failure_overrides_optional_warnings() -> None:
    service, _, _ = make_service(
        runtime=FakeRuntimeInspector(RuntimeObservation(3, 12, 64)),
        command=FakeCommandInspector(UvObservation(None, DiagnosticCode.MISSING_COMMAND)),
        nvidia=FakeNvidiaInspector(NvidiaObservation((), DiagnosticCode.MISSING_COMMAND)),
    )

    report = run(service)

    assert report.results[0].status is DiagnosticStatus.FAIL
    assert report.status is DiagnosticStatus.FAIL
    assert report.exit_code == 1


@pytest.mark.parametrize(
    "error",
    [
        InferenceConnectionError("connection"),
        InferenceTimeoutError("timeout"),
        ModelUnavailableError("unavailable"),
        InferenceProtocolError("protocol"),
        InferenceBackendError("backend"),
        UnsupportedInferenceFeatureError("unsupported"),
        InferenceError("generic"),
    ],
)
def test_each_mapped_inference_error_closes_gateway_once(error: InferenceError) -> None:
    gateway = FakeGateway(error=error)
    service, configured_gateway, closer = make_service(gateway=gateway)

    report = run(service)

    assert report.status is DiagnosticStatus.FAIL
    assert configured_gateway.calls == 1
    assert closer.calls == 1


def test_unexpected_collector_error_closes_gateway_then_propagates() -> None:
    service, gateway, closer = make_service(system=FakeSystemInspector(error=RuntimeError("bug")))

    with pytest.raises(RuntimeError, match="^bug$"):
        run(service)

    assert gateway.calls == 0
    assert closer.calls == 1


def test_construction_does_not_execute_any_collector() -> None:
    runtime = FakeRuntimeInspector()
    system = FakeSystemInspector()
    command = FakeCommandInspector()
    nvidia = FakeNvidiaInspector()
    reference = FakeReferenceInspector()
    gateway = available_gateway()
    closer = FakeCloser()

    service, _, _ = make_service(
        runtime=runtime,
        system=system,
        command=command,
        nvidia=nvidia,
        reference=reference,
        gateway=gateway,
        closer=closer,
    )

    assert service is not None
    assert runtime.calls == []
    assert system.calls == []
    assert command.calls == []
    assert nvidia.calls == []
    assert reference.calls == []
    assert gateway.calls == 0
    assert closer.calls == 0
