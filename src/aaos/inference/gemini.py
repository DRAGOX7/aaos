"""Optional Gemini model gateway (explicit opt-in; the local LM Studio model is the default).

Selecting this gateway sends prompts, which include question text and course-file
excerpts, to Google's Gemini API. Guard rails that keep it narrow:

* the endpoint is a fixed HTTPS URL, never a setting, with no redirects or proxies;
* the API key comes only from the environment, is held in one private attribute and
  one request header, and appears in no telemetry, error message or repr;
* the same request budget checks as the local gateway apply;
* AAOS still validates every model reply with its own strict parsers, so loosening
  the JSON schema Gemini is sent (it supports a subset) weakens nothing.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import httpx2

from aaos.config import AppSettings

from .gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceContextLimitError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)
from .lm_studio import _STRUCTURED_RESPONSE_FORMATS, _estimate_prompt_tokens, _parse_generation
from .models import ChatRequest, ChatResponse, ModelAvailability
from .telemetry import (
    LocalInferenceTelemetryEvent,
    TelemetrySink,
    emit_local_inference_telemetry,
    safe_error_code,
    safe_exception_type,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# JSON-schema keywords Gemini's structured output accepts. Everything else (pattern,
# additionalProperties, minLength, maxLength, ...) is dropped before sending.
_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "description",
        "anyOf",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "format",
        "title",
        "nullable",
    }
)
_GOOGLE_STATUS = re.compile(r"^[A-Z_]{3,40}$")


class GeminiHTTPError(InferenceBackendError):
    """A Gemini HTTP failure that keeps its status code (never its body)."""

    def __init__(self, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.http_status = http_status


def gemini_response_format(openai_format: dict[str, Any]) -> dict[str, Any]:
    """Reduce an AAOS strict ``json_schema`` response format to what Gemini accepts."""
    wrapper = openai_format["json_schema"]
    return {
        "type": "json_schema",
        "json_schema": {"name": wrapper["name"], "schema": _clean_schema(wrapper["schema"])},
    }


def _clean_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key == "oneOf":
            cleaned["anyOf"] = _clean_schema(value)
        elif key not in _SUPPORTED_SCHEMA_KEYS:
            continue
        elif key == "properties" and isinstance(value, dict):
            # Property names are data, not keywords: keep them all.
            cleaned[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        elif key in {"items", "anyOf"}:
            cleaned[key] = _clean_schema(value)
        else:
            cleaned[key] = deepcopy(value)
    return cleaned


class GeminiGateway:
    """Own one async client for Gemini's OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: AppSettings,
        transport: httpx2.AsyncBaseTransport,
        *,
        api_key: str,
        diagnostic_sink: TelemetrySink | None = None,
    ) -> None:
        if settings.model_provider != "gemini" or settings.gemini_model is None:
            raise ValueError("settings must select the gemini provider")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("a Gemini API key is required")
        self._settings = settings
        self._model = settings.gemini_model.removeprefix("models/")
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self._client = httpx2.AsyncClient(
            transport=transport,
            base_url=GEMINI_BASE_URL,
            timeout=settings.lm_studio_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._diagnostic_sink = diagnostic_sink or emit_local_inference_telemetry
        self._closed = False

    def __repr__(self) -> str:
        return f"GeminiGateway(model={self._model!r})"

    async def check_availability(self) -> ModelAvailability:
        """List the account's models once and report whether the configured one exists."""
        response = await self._send("GET", "models", operation="availability")
        try:
            payload = response.json()
            entries = payload["data"]
            names = tuple(
                str(entry["id"]).removeprefix("models/")
                for entry in entries
                if isinstance(entry, dict) and str(entry.get("id", "")).strip()
            )
        except (TypeError, ValueError, KeyError):
            raise InferenceProtocolError("Gemini returned an unreadable model list") from None
        available = self._model in names
        return ModelAvailability(
            reachable=True,
            available=available,
            configured_model=self._model,
            available_models=names,
            loaded_context_lengths=(
                (self._settings.lm_studio_context_length,) if available else ()
            ),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Send one chat request and return validated visible output."""
        if self._closed:
            raise InferenceError("Gemini gateway is closed") from None
        if request.reasoning:
            raise UnsupportedInferenceFeatureError("Reasoning is not supported")
        context_length = self._settings.lm_studio_context_length
        if request.max_output_tokens is not None and request.max_output_tokens > context_length:
            raise ValueError("max_output_tokens exceeds the configured context length")

        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": False,
            "reasoning_effort": "none",
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
            if _estimate_prompt_tokens(request) + request.max_output_tokens > context_length:
                raise InferenceContextLimitError(
                    "Gemini request exceeds the configured context window"
                )
        if request.structured_output == "json_schema":
            payload["response_format"] = gemini_response_format(
                _STRUCTURED_RESPONSE_FORMATS[request.structured_output_schema]
            )

        try:
            response = await self._send(
                "POST",
                "chat/completions",
                operation="generation",
                payload=payload,
                timeout_seconds=request.request_timeout_seconds,
            )
        except GeminiHTTPError as error:
            if error.http_status != 400 or "response_format" not in payload:
                raise
            # Gemini accepts only part of JSON Schema and may reject a structured
            # request. Retry once without it: AAOS validates every reply itself.
            del payload["response_format"]
            response = await self._send(
                "POST",
                "chat/completions",
                operation="generation",
                payload=payload,
                timeout_seconds=request.request_timeout_seconds,
            )
        try:
            return _parse_generation(response.json())
        except (TypeError, ValueError):
            protocol_error = InferenceProtocolError(
                "Gemini returned invalid data during generation"
            )
            self._emit_failure("generation", protocol_error)
            raise protocol_error from None

    async def _send(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> httpx2.Response:
        if self._closed:
            raise InferenceError("Gemini gateway is closed") from None
        options: dict[str, Any] = {"headers": self._headers}
        if payload is not None:
            options["json"] = payload
        if timeout_seconds is not None:
            options["timeout"] = timeout_seconds
        translated: InferenceError
        try:
            response = await self._client.request(method, path, **options)
        except httpx2.TimeoutException:
            translated = InferenceTimeoutError(
                f"Gemini request timed out during {operation} for model '{self._model}'"
            )
        except httpx2.RequestError:
            translated = InferenceConnectionError(
                f"Gemini connection failed during {operation} for model '{self._model}'"
            )
        else:
            if response.is_success:
                self._emit(
                    LocalInferenceTelemetryEvent(
                        operation="INFERENCE_REQUEST",
                        model_identifier=self._model,
                        model_role="answer",
                        lifecycle_state="completed",
                        http_status=response.status_code,
                        backend_category="success",
                    )
                )
                return response
            error = self._backend_error(response, operation)
            self._emit_failure(
                operation,
                error,
                http_status=response.status_code,
                backend_category=_category(response),
            )
            raise error from None
        self._emit_failure(operation, translated)
        raise translated from None

    def _backend_error(self, response: httpx2.Response, operation: str) -> InferenceError:
        status = response.status_code
        model = self._model
        if status in {401, 403}:
            return GeminiHTTPError(
                f"Gemini rejected the API key during {operation} (status {status})",
                http_status=status,
            )
        if status == 404:
            return ModelUnavailableError(f"Gemini model '{model}' is unavailable")
        if status == 429:
            return GeminiHTTPError(
                f"Gemini rate limit or quota reached during {operation} (status 429)",
                http_status=status,
            )
        return GeminiHTTPError(
            f"Gemini backend failed during {operation} for model '{model}' (status {status})",
            http_status=status,
        )

    def _emit_failure(
        self,
        operation: str,
        error: BaseException,
        *,
        http_status: int | None = None,
        backend_category: str | None = None,
    ) -> None:
        self._emit(
            LocalInferenceTelemetryEvent(
                operation="INFERENCE_REQUEST",
                model_identifier=self._model,
                model_role="answer",
                lifecycle_state=f"failed:{operation}",
                exception_type=safe_exception_type(error),
                safe_error_code=safe_error_code(error),
                http_status=http_status,
                backend_category=backend_category,
            )
        )

    def _emit(self, event: LocalInferenceTelemetryEvent) -> None:
        try:
            self._diagnostic_sink(event)
        except Exception:
            return

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()


def _category(response: httpx2.Response) -> str:
    """http_<status>, plus Google's own error.status word when it is a safe token."""
    category = f"http_{response.status_code}"
    try:
        status = response.json()["error"]["status"]
    except (TypeError, ValueError, KeyError):
        return category
    if isinstance(status, str) and _GOOGLE_STATUS.match(status):
        return f"{category}_{status}"
    return category
