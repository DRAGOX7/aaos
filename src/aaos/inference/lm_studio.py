"""Offline-testable LM Studio adapter for local inference diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import httpx2

from aaos.config import AppSettings

from .gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)
from .models import ChatRequest, ChatResponse, ModelAvailability, TokenUsage

_SUPPORTED_MODEL_TYPES = frozenset({"llm", "embedding"})
_ParsedResponse = TypeVar("_ParsedResponse")


class LMStudioGateway:
    """Own one async client and report the configured model's loaded state."""

    def __init__(
        self,
        settings: AppSettings,
        transport: httpx2.AsyncBaseTransport,
    ) -> None:
        """Take exclusive ownership of a dedicated async transport."""
        self._settings = settings
        self._client = httpx2.AsyncClient(
            transport=transport,
            base_url=settings.lm_studio_base_url,
            timeout=settings.lm_studio_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._closed = False

    async def check_availability(self) -> ModelAvailability:
        """Query native inventory once and validate all data before reporting it."""
        operation = "availability"
        model = self._settings.lm_studio_model
        response = await self._send(
            method="GET",
            path="/api/v1/models",
            operation=operation,
        )
        return _parse_success_response(
            response,
            operation=operation,
            model=model,
            parser=lambda payload: _parse_availability(
                payload, configured_model=model
            ),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Send one plain chat request and return validated visible output."""
        operation = "generation"
        model = self._settings.lm_studio_model

        if request.reasoning:
            raise UnsupportedInferenceFeatureError(
                "Reasoning is not supported in Phase 0E"
            )
        if (
            request.max_output_tokens is not None
            and request.max_output_tokens > self._settings.lm_studio_context_length
        ):
            raise ValueError("max_output_tokens exceeds the configured context length")

        request_payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": False,
            "reasoning_effort": "none",
        }
        if request.max_output_tokens is not None:
            request_payload["max_tokens"] = request.max_output_tokens

        response = await self._send(
            method="POST",
            path="/v1/chat/completions",
            operation=operation,
            request_payload=request_payload,
        )
        return _parse_success_response(
            response,
            operation=operation,
            model=model,
            parser=_parse_generation,
        )

    async def _send(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        request_payload: dict[str, object] | None = None,
    ) -> httpx2.Response:
        """Execute one request and expose only fixed AAOS failures."""
        model = self._settings.lm_studio_model
        if self._closed:
            raise InferenceError("LM Studio gateway is closed") from None

        try:
            if request_payload is None:
                response = await self._client.request(method, path)
            else:
                response = await self._client.request(
                    method, path, json=request_payload
                )
        except httpx2.TimeoutException:
            raise InferenceTimeoutError(
                f"LM Studio request timed out during {operation} for model '{model}'"
            ) from None
        except httpx2.RequestError:
            raise InferenceConnectionError(
                f"LM Studio connection failed during {operation} for model '{model}'"
            ) from None

        if not response.is_success:
            _raise_backend_error(
                response,
                operation=operation,
                model=model,
            )
        return response

    async def aclose(self) -> None:
        """Close the exclusively owned client at most once."""
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()


def _raise_backend_error(
    response: httpx2.Response,
    *,
    operation: str,
    model: str,
) -> None:
    if operation == "generation":
        backend_error = _read_structured_backend_error(response)
        if backend_error is not None:
            if (
                backend_error.get("code") == "model_not_found"
                or backend_error.get("type") == "model_not_found"
            ):
                raise ModelUnavailableError(
                    f"LM Studio model '{model}' is unavailable during generation"
                ) from None
            if backend_error.get("param") == "reasoning_effort":
                raise UnsupportedInferenceFeatureError(
                    "LM Studio does not support required reasoning-off generation "
                    f"for model '{model}'"
                ) from None

    raise InferenceBackendError(
        f"LM Studio backend failed during {operation} for model '{model}' "
        f"(status {response.status_code})"
    ) from None


def _read_structured_backend_error(
    response: httpx2.Response,
) -> dict[Any, Any] | None:
    try:
        payload: object = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    backend_error = payload.get("error")
    return backend_error if isinstance(backend_error, dict) else None


def _parse_success_response(
    response: httpx2.Response,
    *,
    operation: str,
    model: str,
    parser: Callable[[object], _ParsedResponse],
) -> _ParsedResponse:
    try:
        payload: object = response.json()
        return parser(payload)
    except (TypeError, ValueError):
        raise InferenceProtocolError(
            f"LM Studio returned invalid data during {operation} for model '{model}'"
        ) from None


def _parse_availability(payload: object, *, configured_model: str) -> ModelAvailability:
    if not isinstance(payload, dict):
        raise ValueError("inventory must be an object")

    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("models must be a list")

    available_models: list[str] = []
    loaded_context_lengths: list[int] = []

    for native_model in models:
        if not isinstance(native_model, dict):
            raise ValueError("model must be an object")

        key = native_model.get("key")
        model_type = native_model.get("type")
        loaded_instances = native_model.get("loaded_instances")

        if not isinstance(key, str) or not key.strip():
            raise ValueError("model key must be a non-empty string")
        if not isinstance(model_type, str) or model_type not in _SUPPORTED_MODEL_TYPES:
            raise ValueError("model type is unsupported")
        if not isinstance(loaded_instances, list):
            raise ValueError("loaded_instances must be a list")

        instance_context_lengths = _parse_loaded_context_lengths(loaded_instances)
        _validate_reasoning_capability(native_model)
        available_models.append(key)

        if key == configured_model and model_type == "llm":
            loaded_context_lengths.extend(instance_context_lengths)

    context_tuple = tuple(loaded_context_lengths)
    return ModelAvailability(
        reachable=True,
        available=bool(context_tuple),
        configured_model=configured_model,
        available_models=tuple(available_models),
        loaded_context_lengths=context_tuple,
    )


def _parse_generation(payload: object) -> ChatResponse:
    if not isinstance(payload, dict):
        raise ValueError("generation response must be an object")

    model = payload.get("model")
    choices = payload.get("choices")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(choices, list) or not choices:
        raise ValueError("choices must be a non-empty list")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    if "tool_calls" in message:
        raise ValueError("tool calls are not supported")
    for reasoning_field in ("reasoning", "reasoning_content"):
        reasoning_value = message.get(reasoning_field)
        if reasoning_value not in (None, "", [], {}):
            raise ValueError("reasoning content is not supported")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")

    usage = _parse_usage(payload.get("usage")) if "usage" in payload else None
    return ChatResponse(content=content, model_identifier=model, usage=usage)


def _parse_usage(payload: object) -> TokenUsage | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("usage must be an object")
    prompt_tokens = _parse_token_count(payload.get("prompt_tokens"))
    completion_tokens = _parse_token_count(payload.get("completion_tokens"))
    total_tokens = _parse_token_count(payload.get("total_tokens"))
    return TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _parse_token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    return value


def _parse_loaded_context_lengths(loaded_instances: list[Any]) -> list[int]:
    context_lengths: list[int] = []
    for loaded_instance in loaded_instances:
        if not isinstance(loaded_instance, dict):
            raise ValueError("loaded instance must be an object")
        config = loaded_instance.get("config")
        if not isinstance(config, dict):
            raise ValueError("loaded instance config must be an object")
        context_length = config.get("context_length")
        if (
            not isinstance(context_length, int)
            or isinstance(context_length, bool)
            or context_length <= 0
        ):
            raise ValueError("context_length must be a positive integer")
        context_lengths.append(context_length)
    return context_lengths


def _validate_reasoning_capability(native_model: dict[Any, Any]) -> None:
    if "capabilities" not in native_model:
        return

    capabilities = native_model["capabilities"]
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    if "reasoning" not in capabilities:
        return

    reasoning = capabilities["reasoning"]
    if not isinstance(reasoning, dict):
        raise ValueError("reasoning capability must be an object")

    allowed_options = reasoning.get("allowed_options")
    default = reasoning.get("default")
    if (
        not isinstance(allowed_options, list)
        or not allowed_options
        or any(not isinstance(option, str) or not option for option in allowed_options)
    ):
        raise ValueError("reasoning allowed_options must contain strings")
    if not isinstance(default, str) or default not in allowed_options:
        raise ValueError("reasoning default must be an allowed option")
