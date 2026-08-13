"""Required LM Studio diagnostics derived from the model gateway contract."""

from aaos.config import AppSettings
from aaos.doctor.models import DiagnosticCode, DiagnosticFact, DiagnosticResult, DiagnosticStatus
from aaos.inference import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelGateway,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)


async def model_diagnostic_results(
    gateway: ModelGateway, settings: AppSettings
) -> tuple[DiagnosticResult, DiagnosticResult, DiagnosticResult]:
    """Translate one safe gateway availability result into required diagnostics."""
    try:
        availability = await gateway.check_availability()
    except InferenceConnectionError:
        return _failure_results(DiagnosticCode.CONNECTION_FAILED)
    except InferenceTimeoutError:
        return _failure_results(DiagnosticCode.TIMEOUT)
    except ModelUnavailableError:
        return _failure_results(DiagnosticCode.MODEL_UNAVAILABLE)
    except InferenceProtocolError:
        return _failure_results(DiagnosticCode.PROTOCOL_FAILED)
    except InferenceBackendError:
        return _failure_results(DiagnosticCode.BACKEND_FAILED)
    except UnsupportedInferenceFeatureError:
        return _failure_results(DiagnosticCode.UNSUPPORTED_INFERENCE)
    except InferenceError:
        return _failure_results(DiagnosticCode.INFERENCE_FAILED)

    if not availability.reachable:
        return _failure_results(DiagnosticCode.CONNECTION_FAILED)

    lm_studio_result = DiagnosticResult(
        "lm_studio", DiagnosticStatus.PASS, "LM Studio is reachable"
    )
    configured_model_fact = DiagnosticFact("configured_model", settings.lm_studio_model)
    if not availability.available:
        return (
            lm_studio_result,
            DiagnosticResult(
                "configured_model",
                DiagnosticStatus.FAIL,
                "Configured model is unavailable",
                facts=(configured_model_fact,),
                code=DiagnosticCode.MODEL_UNAVAILABLE,
            ),
            _not_evaluated_result("loaded_context", "Loaded context was not evaluated"),
        )

    context_ok = any(
        loaded >= settings.lm_studio_context_length
        for loaded in availability.loaded_context_lengths
    )
    loaded_context_fact = DiagnosticFact(
        "required_context_length", settings.lm_studio_context_length
    )
    loaded_context_result = DiagnosticResult(
        "loaded_context",
        DiagnosticStatus.PASS if context_ok else DiagnosticStatus.FAIL,
        "Loaded context meets configured requirement"
        if context_ok
        else "Loaded context is below configured requirement",
        facts=(loaded_context_fact,),
        code=None if context_ok else DiagnosticCode.INSUFFICIENT_CONTEXT,
    )
    return (
        lm_studio_result,
        DiagnosticResult(
            "configured_model",
            DiagnosticStatus.PASS,
            "Configured model is available",
            facts=(configured_model_fact,),
        ),
        loaded_context_result,
    )


def _failure_results(
    code: DiagnosticCode,
) -> tuple[DiagnosticResult, DiagnosticResult, DiagnosticResult]:
    return (
        DiagnosticResult(
            "lm_studio", DiagnosticStatus.FAIL, "LM Studio availability check failed", code=code
        ),
        _not_evaluated_result("configured_model", "Configured model was not evaluated"),
        _not_evaluated_result("loaded_context", "Loaded context was not evaluated"),
    )


def _not_evaluated_result(check_id: str, summary: str) -> DiagnosticResult:
    return DiagnosticResult(
        check_id,
        DiagnosticStatus.WARN,
        summary,
        code=DiagnosticCode.NOT_EVALUATED,
    )
