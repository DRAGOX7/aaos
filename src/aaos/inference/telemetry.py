"""Safe, local-only diagnostics for model lifecycle and inference failures."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

TelemetryOperation = Literal["LOAD", "READY_CHECK", "INFERENCE_REQUEST", "UNLOAD", "FINAL_CLEANUP"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
_LOGGER = logging.getLogger("aaos.inference.telemetry")


@dataclass(frozen=True, slots=True)
class LocalInferenceTelemetryEvent:
    """A bounded event containing no prompt, response, or credential data."""

    operation: TelemetryOperation
    model_identifier: str | None = None
    model_role: str | None = None
    lifecycle_state: str | None = None
    exception_type: str | None = None
    safe_error_code: str | None = None
    http_status: int | None = None
    backend_category: str | None = None
    closer_identity: str | None = None
    cleanup_already_completed: bool | None = None
    model_listed_before: bool | None = None
    model_listed_after: bool | None = None
    context_length: int | None = None

    def as_dict(self) -> dict[str, object | None]:
        """Return only the explicitly defined safe fields."""
        return asdict(self)


TelemetrySink = Callable[[LocalInferenceTelemetryEvent], None]


def emit_local_inference_telemetry(event: LocalInferenceTelemetryEvent) -> None:
    """Publish one safe event through the standard local logging pipeline."""
    level = logging.WARNING if event.exception_type is not None else logging.INFO
    _LOGGER.log(level, "local inference telemetry", extra={"aaos_event": event.as_dict()})


def safe_exception_type(error: BaseException) -> str:
    """Return a bounded exception class name without exception text."""
    name = _SAFE_NAME.sub("_", type(error).__name__)[:80]
    return name or "UnknownError"


def safe_error_code(error: BaseException) -> str:
    """Map known local failures to stable diagnostic categories."""
    mapping = {
        "ModelLifecycleError": "MODEL_LIFECYCLE_ERROR",
        "ModelUnavailableError": "MODEL_UNAVAILABLE",
        "InferenceTimeoutError": "INFERENCE_TIMEOUT",
        "InferenceConnectionError": "INFERENCE_CONNECTION",
        "InferenceContextLimitError": "INFERENCE_CONTEXT_LIMIT",
        "InferenceProtocolError": "INFERENCE_PROTOCOL_ERROR",
        "InferenceBackendError": "INFERENCE_BACKEND_ERROR",
        "UnsupportedInferenceFeatureError": "UNSUPPORTED_INFERENCE_FEATURE",
        "TimeoutError": "MODEL_LIFECYCLE_ERROR",
    }
    return mapping.get(type(error).__name__, "LOCAL_INFERENCE_ERROR")


def safe_backend_category(error: BaseException) -> str:
    """Return a fixed category derived from the safe exception class."""
    name = type(error).__name__
    return {
        "InferenceTimeoutError": "timeout",
        "InferenceConnectionError": "connection",
        "InferenceContextLimitError": "context_limit",
        "InferenceProtocolError": "protocol",
        "InferenceBackendError": "backend",
        "ModelUnavailableError": "model_not_found",
        "ModelLifecycleError": "lifecycle",
        "TimeoutError": "timeout",
    }.get(name, "local_error")


__all__ = [
    "LocalInferenceTelemetryEvent",
    "TelemetryOperation",
    "TelemetrySink",
    "emit_local_inference_telemetry",
    "safe_backend_category",
    "safe_error_code",
    "safe_exception_type",
]
