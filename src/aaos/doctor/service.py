"""Deterministic aggregation of the approved local doctor diagnostics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from aaos.config import AppSettings
from aaos.doctor.commands import CommandInspector
from aaos.doctor.model_check import model_diagnostic_results
from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    DoctorReport,
    NvidiaObservation,
    UvObservation,
)
from aaos.doctor.nvidia import nvidia_result
from aaos.doctor.reference import ReferenceInspector, reference_result
from aaos.doctor.runtime import RuntimeInspector, runtime_results
from aaos.doctor.system import SystemInspector, system_results
from aaos.inference import ModelGateway


class NvidiaInspector(Protocol):
    """Read the already parsed optional NVIDIA observation."""

    def inspect(self) -> NvidiaObservation: ...


class DoctorService:
    """Collect the fixed Phase 0F diagnostic sequence without remediation."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        runtime_inspector: RuntimeInspector,
        system_inspector: SystemInspector,
        command_inspector: CommandInspector,
        nvidia_inspector: NvidiaInspector,
        reference_inspector: ReferenceInspector,
        model_gateway: ModelGateway,
        close_model_gateway: Callable[[], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._runtime_inspector = runtime_inspector
        self._system_inspector = system_inspector
        self._command_inspector = command_inspector
        self._nvidia_inspector = nvidia_inspector
        self._reference_inspector = reference_inspector
        self._model_gateway = model_gateway
        self._close_model_gateway = close_model_gateway

    async def run(self) -> DoctorReport:
        """Return all approved checks in their stable order and close the gateway."""
        try:
            runtime_observation = self._runtime_inspector.inspect()
            system_observation = self._system_inspector.inspect()
            uv_observation = self._command_inspector.inspect_uv()
            nvidia_observation = self._nvidia_inspector.inspect()
            reference_observation = self._reference_inspector.compare(
                system_observation, nvidia_observation
            )
            model_results = await model_diagnostic_results(self._model_gateway, self._settings)

            return DoctorReport(
                (
                    *runtime_results(runtime_observation),
                    *system_results(system_observation),
                    _uv_result(uv_observation),
                    nvidia_result(nvidia_observation),
                    reference_result(reference_observation),
                    *model_results,
                )
            )
        finally:
            await self._close_model_gateway()


def _uv_result(observation: UvObservation) -> DiagnosticResult:
    if observation.version is not None and observation.code is None:
        return DiagnosticResult(
            "uv",
            DiagnosticStatus.PASS,
            "uv version available",
            facts=(DiagnosticFact("uv_version", observation.version),),
        )
    return DiagnosticResult(
        "uv",
        DiagnosticStatus.WARN,
        "uv version unavailable",
        code=observation.code or DiagnosticCode.MALFORMED_OUTPUT,
    )
