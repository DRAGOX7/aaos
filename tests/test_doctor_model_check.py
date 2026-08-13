from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from aaos.config import AppSettings
from aaos.doctor.model_check import model_diagnostic_results
from aaos.doctor.models import DiagnosticCode, DiagnosticFact, DiagnosticResult, DiagnosticStatus
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


@dataclass
class FakeGateway:
    availability: ModelAvailability | None = None
    error: InferenceError | None = None
    availability_calls: int = 0
    generate_calls: int = 0

    async def check_availability(self) -> ModelAvailability:
        self.availability_calls += 1
        if self.error is not None:
            raise self.error
        assert self.availability is not None
        return self.availability

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.generate_calls += 1
        raise AssertionError("model diagnostics must not generate")


def available_model(*, loaded_context_lengths: tuple[int, ...] = (8192,)) -> ModelAvailability:
    return ModelAvailability(
        reachable=True,
        available=True,
        configured_model="untrusted-gateway-model",
        available_models=("untrusted-gateway-model",),
        loaded_context_lengths=loaded_context_lengths,
    )


def run_checks(gateway: FakeGateway, settings: AppSettings) -> tuple[DiagnosticResult, ...]:
    return asyncio.run(model_diagnostic_results(gateway, settings))


def test_available_model_with_sufficient_configured_context_passes_all_required_checks() -> None:
    gateway = FakeGateway(availability=available_model())

    results = run_checks(gateway, AppSettings(lm_studio_context_length=4096))

    assert [result.check_id for result in results] == [
        "lm_studio",
        "configured_model",
        "loaded_context",
    ]
    assert [result.status for result in results] == [DiagnosticStatus.PASS] * 3
    assert gateway.availability_calls == 1
    assert gateway.generate_calls == 0


def test_loaded_context_uses_settings_requirement_not_a_literal_default() -> None:
    gateway = FakeGateway(availability=available_model(loaded_context_lengths=(8192, 16384)))

    results = run_checks(gateway, AppSettings(lm_studio_context_length=12288))

    assert [result.status for result in results] == [DiagnosticStatus.PASS] * 3
    assert results[2].facts == (DiagnosticFact("required_context_length", 12288),)


def test_insufficient_loaded_context_fails_only_context_check() -> None:
    gateway = FakeGateway(availability=available_model(loaded_context_lengths=(2048, 4096)))

    results = run_checks(gateway, AppSettings(lm_studio_context_length=8192))

    assert [result.status for result in results] == [
        DiagnosticStatus.PASS,
        DiagnosticStatus.PASS,
        DiagnosticStatus.FAIL,
    ]
    assert results[2].code is DiagnosticCode.INSUFFICIENT_CONTEXT
    assert results[2].facts == (DiagnosticFact("required_context_length", 8192),)


def test_unavailable_model_marks_context_not_evaluated() -> None:
    gateway = FakeGateway(
        availability=ModelAvailability(
            reachable=True,
            available=False,
            configured_model="untrusted-gateway-model",
            available_models=(),
        )
    )

    results = run_checks(gateway, AppSettings(lm_studio_model="configured/model"))

    assert [result.status for result in results] == [
        DiagnosticStatus.PASS,
        DiagnosticStatus.FAIL,
        DiagnosticStatus.WARN,
    ]
    assert results[1].code is DiagnosticCode.MODEL_UNAVAILABLE
    assert results[1].facts == (DiagnosticFact("configured_model", "configured/model"),)
    assert results[2].code is DiagnosticCode.NOT_EVALUATED


def test_configured_model_identity_comes_from_settings() -> None:
    gateway = FakeGateway(availability=available_model())

    results = run_checks(gateway, AppSettings(lm_studio_model="settings/model"))

    assert results[1].facts == (DiagnosticFact("configured_model", "settings/model"),)
    rendered = "\n".join(
        " ".join([result.summary, *(str(fact.value) for fact in result.facts)])
        for result in results
    )
    assert "untrusted-gateway-model" not in rendered


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            InferenceConnectionError("SENTINEL connection native error"),
            DiagnosticCode.CONNECTION_FAILED,
        ),
        (InferenceTimeoutError("SENTINEL timeout native error"), DiagnosticCode.TIMEOUT),
        (
            ModelUnavailableError("SENTINEL unavailable native error"),
            DiagnosticCode.MODEL_UNAVAILABLE,
        ),
        (InferenceProtocolError("SENTINEL protocol native error"), DiagnosticCode.PROTOCOL_FAILED),
        (InferenceBackendError("SENTINEL backend native error"), DiagnosticCode.BACKEND_FAILED),
        (
            UnsupportedInferenceFeatureError("SENTINEL unsupported native error"),
            DiagnosticCode.UNSUPPORTED_INFERENCE,
        ),
        (InferenceError("SENTINEL generic native error"), DiagnosticCode.INFERENCE_FAILED),
    ],
)
def test_safe_inference_errors_produce_one_failure_and_redacted_dependents(
    error: InferenceError, expected_code: DiagnosticCode
) -> None:
    gateway = FakeGateway(error=error)

    results = run_checks(gateway, AppSettings())

    assert [result.status for result in results] == [
        DiagnosticStatus.FAIL,
        DiagnosticStatus.WARN,
        DiagnosticStatus.WARN,
    ]
    assert results[0].code is expected_code
    assert [result.code for result in results[1:]] == [DiagnosticCode.NOT_EVALUATED] * 2
    assert gateway.availability_calls == 1
    assert gateway.generate_calls == 0


def test_unreachable_availability_marks_dependents_not_evaluated() -> None:
    gateway = FakeGateway(
        availability=ModelAvailability(
            reachable=False,
            available=False,
            configured_model="untrusted-gateway-model",
            available_models=(),
        )
    )

    results = run_checks(gateway, AppSettings())

    assert [result.status for result in results] == [
        DiagnosticStatus.FAIL,
        DiagnosticStatus.WARN,
        DiagnosticStatus.WARN,
    ]
    assert results[0].code is DiagnosticCode.CONNECTION_FAILED
    assert [result.code for result in results[1:]] == [DiagnosticCode.NOT_EVALUATED] * 2


def test_result_text_never_contains_untrusted_gateway_or_exception_details() -> None:
    sentinel_values = (
        "SENTINEL prompt do-not-expose",
        "SENTINEL response body do-not-expose",
        "SENTINEL Authorization header do-not-expose",
        "SENTINEL token do-not-expose",
        "SENTINEL native exception do-not-expose",
        "SENTINEL traceback do-not-expose",
    )
    gateway = FakeGateway(error=InferenceBackendError(" ".join(sentinel_values)))

    results = run_checks(gateway, AppSettings())

    rendered = "\n".join(
        f"{result.summary} {' '.join(str(fact.value) for fact in result.facts)}"
        for result in results
    )
    assert all(value not in rendered for value in sentinel_values)
