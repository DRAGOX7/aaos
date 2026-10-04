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

import asyncio
import logging
import re
import time
from collections.abc import Callable, Iterable
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
from .lm_studio import _STRUCTURED_RESPONSE_FORMATS, _estimate_prompt_tokens, _parse_usage
from .models import ChatRequest, ChatResponse, ModelAvailability, TokenUsage
from .telemetry import (
    LocalInferenceTelemetryEvent,
    TelemetrySink,
    emit_local_inference_telemetry,
    safe_error_code,
    safe_exception_type,
)

_LOGGER = logging.getLogger("aaos.inference.gemini")

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
# Seconds to wait before each retry of a 503: Google's "high demand" spikes are
# short but can outlast a few seconds. Three retries at most (10 s in all), so a real
# outage still fails promptly.
_OVERLOAD_RETRY_DELAYS = (1.0, 3.0, 6.0)
# A 429 on the free tier is usually a per-minute limit: Google says how long to wait. Wait
# that long (never longer than the cap) at most twice; a daily limit fails at once.
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_DEFAULT_WAIT = 12.0
_RATE_LIMIT_WAIT_CAP = 30.0
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
_GOOGLE_STATUS = re.compile(r"^[A-Z_]{3,40}$")
_FINISH_REASON = re.compile(r"^[A-Za-z_]{1,30}$")


class GeminiHTTPError(InferenceBackendError):
    """A Gemini HTTP failure that keeps its status code (never its body)."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int,
        retry_after: float | None = None,
        daily_limit: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        # For a 429: how long Google asked us to wait, and whether the limit is per day.
        self.retry_after = retry_after
        self.daily_limit = daily_limit


class _RequestPacer:
    """Space requests out so a free-tier per-minute limit is not hit in the first place.

    Process-wide on purpose: the assistant and the document runtime hold separate
    gateways but share one API key and therefore one limit.
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._next_slot = 0.0

    async def wait(self, min_interval: float) -> None:
        if min_interval <= 0:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            delay = self._next_slot - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_slot = max(now, self._next_slot) + min_interval


_PACER = _RequestPacer()


# Names in Google's model list that are not chat models (speech, images, live audio,
# embeddings, video, music, agents): never offered as the answering model.
_NON_CHAT_TOKENS = (
    "tts",
    "image",
    "live",
    "embedding",
    "transcribe",
    "robotics",
    "computer-use",
    "native-audio",
    "veo",
    "lyria",
    "aqa",
    "deep-research",
    "antigravity",
    "nano-banana",
)


def chat_model_names(names: Iterable[str]) -> tuple[str, ...]:
    """The Gemini and Gemma chat models in ``names``, sorted, without the "models/" prefix."""
    kept = {
        name
        for name in (raw.removeprefix("models/") for raw in names)
        if name.startswith(("gemini", "gemma")) and not any(t in name for t in _NON_CHAT_TOKENS)
    }
    return tuple(sorted(kept))


async def fetch_gemini_model_names(
    api_key: str, transport: httpx2.AsyncBaseTransport | None = None
) -> tuple[str, ...]:
    """List the chat models this key can use (the same fixed endpoint, no redirects)."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("a Gemini API key is required")
    try:
        async with httpx2.AsyncClient(
            transport=transport or httpx2.AsyncHTTPTransport(),
            base_url=GEMINI_BASE_URL,
            timeout=15.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(
                "models", headers={"Authorization": f"Bearer {api_key.strip()}"}
            )
    except httpx2.HTTPError:
        raise InferenceConnectionError("Gemini model list could not be reached") from None
    if not response.is_success:
        raise InferenceBackendError(f"Gemini model list failed (status {response.status_code})")
    try:
        ids = [
            str(entry["id"])
            for entry in response.json()["data"]
            if isinstance(entry, dict) and str(entry.get("id", "")).strip()
        ]
    except (TypeError, ValueError, KeyError):
        raise InferenceProtocolError("Gemini returned an unreadable model list") from None
    return chat_model_names(ids)


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
        overload_retry_delays: tuple[float, ...] = _OVERLOAD_RETRY_DELAYS,
        on_failure: Callable[[str], None] | None = None,
        on_success: Callable[[], None] | None = None,
        min_interval_seconds: float = 0.0,
        rate_limit_retries: int = _RATE_LIMIT_RETRIES,
        rate_limit_default_wait: float = _RATE_LIMIT_DEFAULT_WAIT,
        rate_limit_wait_cap: float = _RATE_LIMIT_WAIT_CAP,
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
        self._overload_retry_delays = overload_retry_delays
        self._on_failure = on_failure
        self._on_success = on_success
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._rate_limit_retries = rate_limit_retries
        self._rate_limit_default_wait = rate_limit_default_wait
        self._rate_limit_wait_cap = rate_limit_wait_cap
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

        # Gemini accepts only part of what AAOS asks for and answers HTTP 400 when a
        # setting is not supported by the chosen model. Retry with less, never more:
        # first without the JSON schema, then also without reasoning_effort. AAOS
        # validates every reply itself, so a reply from a reduced request is still
        # checked exactly the same way.
        attempts = [payload]
        if "response_format" in payload:
            attempts.append({k: v for k, v in payload.items() if k != "response_format"})
        attempts.append({k: v for k, v in attempts[-1].items() if k != "reasoning_effort"})
        for index, attempt in enumerate(attempts):
            try:
                response = await self._post_with_overload_retry(
                    attempt, request.request_timeout_seconds
                )
                break
            except GeminiHTTPError as error:
                if error.http_status != 400 or index == len(attempts) - 1:
                    raise
        try:
            body: object = response.json()
        except ValueError:
            body = None
        reply, reason = _read_reply(body, self._model)
        if reply is None:
            protocol_error = InferenceProtocolError(
                "Gemini returned invalid data during generation"
            )
            self._emit_failure("generation", protocol_error, detail=f"reply unreadable: {reason}")
            raise protocol_error from None
        self._report(self._on_success)
        return reply

    async def _post_with_overload_retry(
        self, payload: dict[str, object], timeout_seconds: float | None
    ) -> httpx2.Response:
        """POST one chat request, waiting out a 503 "high demand" spike or a 429 limit.

        A 503 is retried after short fixed waits. A 429 waits as long as Google asks
        (capped), at most twice, and never on a daily limit; everything else fails now.
        """
        overloads = 0
        rate_limits = 0
        while True:
            await _PACER.wait(self._min_interval)
            try:
                return await self._send(
                    "POST",
                    "chat/completions",
                    operation="generation",
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
            except GeminiHTTPError as error:
                if error.http_status == 503 and overloads < len(self._overload_retry_delays):
                    delay = self._overload_retry_delays[overloads]
                    overloads += 1
                elif (
                    error.http_status == 429
                    and not error.daily_limit
                    and (rate_limits < self._rate_limit_retries)
                ):
                    delay = (
                        error.retry_after
                        if error.retry_after is not None
                        else self._rate_limit_default_wait
                    )
                    if delay > self._rate_limit_wait_cap:
                        raise
                    rate_limits += 1
                else:
                    raise
                await asyncio.sleep(delay)

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
            retry_after, daily_limit = _rate_limit_hint(response)
            return GeminiHTTPError(
                f"Gemini rate limit or quota reached during {operation} (status 429)",
                http_status=status,
                retry_after=retry_after,
                daily_limit=daily_limit,
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
        detail: str | None = None,
    ) -> None:
        # GeminiHTTPError is a subclass the shared name table does not know.
        error_code = (
            "INFERENCE_BACKEND_ERROR"
            if isinstance(error, InferenceBackendError)
            else safe_error_code(error)
        )
        self._emit(
            LocalInferenceTelemetryEvent(
                operation="INFERENCE_REQUEST",
                model_identifier=self._model,
                model_role="answer",
                lifecycle_state=f"failed:{operation}",
                exception_type=safe_exception_type(error),
                safe_error_code=error_code,
                http_status=http_status,
                backend_category=backend_category,
            )
        )
        # The telemetry event above carries structured fields that the default log format
        # does not print, so say the safe facts in the message itself: no prompt, reply,
        # key or provider text, only the operation, status, category and error kind.
        _LOGGER.warning(
            "Gemini request failed: operation=%s model=%s http_status=%s category=%s error=%s"
            " detail=%s",
            operation,
            self._model,
            http_status,
            backend_category,
            error_code,
            detail,
        )
        # The same safe facts, for the model panel in the page: no prompt, reply or key.
        what = backend_category or error_code.lower()
        self._report(
            self._on_failure,
            f"Gemini {operation} failed: {what}" + (f" ({detail})" if detail else ""),
        )

    def _report(self, callback: Callable[..., None] | None, *args: str) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            return

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


def _read_reply(payload: object, configured_model: str) -> tuple[ChatResponse | None, str]:
    """Take the answer text from a reply and ignore everything else Google adds.

    Only ``choices[0].message.content`` matters. Extra fields (thinking summaries,
    tool-call stubs, per-model usage details) are not errors, and token usage is
    best-effort: it is informational, so a missing or odd count never fails an answer.
    Returns the reply, or None and a short safe reason.
    """
    if not isinstance(payload, dict):
        return None, "not_an_object"
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "no_choices"
    first = choices[0]
    finish = first.get("finish_reason")
    finish_text = (
        f" finish_reason={finish}"
        if isinstance(finish, str) and _FINISH_REASON.match(finish)
        else ""
    )
    message = first.get("message")
    if not isinstance(message, dict):
        return None, "no_message" + finish_text
    content = message.get("content")
    if isinstance(content, list):  # OpenAI-style content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        return None, "empty_content" + finish_text
    # The identity is the model that was asked for: Google may echo an alias or a
    # dated variant ("models/gemini-...", "...-001"), and the callers that check
    # which model answered compare against the configured name.
    model_id = configured_model
    usage: TokenUsage | None
    try:
        usage = _parse_usage(payload.get("usage"))
    except (TypeError, ValueError):
        usage = None
    return ChatResponse(content=content, model_identifier=model_id, usage=usage), "ok"


def _rate_limit_hint(response: httpx2.Response) -> tuple[float | None, bool]:
    """How long Google asked us to wait, and whether the exhausted limit is per day.

    Reads only Google's standard error details (RetryInfo and QuotaFailure); anything
    missing or odd gives (None, False) and the caller falls back to its default wait.
    """
    try:
        body = response.json()
        if isinstance(body, list) and body:
            body = body[0]
        details = body["error"].get("details", [])
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None, False
    retry_after: float | None = None
    daily = False
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        delay = item.get("retryDelay")
        if isinstance(delay, str) and (match := _RETRY_DELAY.match(delay)):
            retry_after = float(match.group(1))
        violations = item.get("violations")
        for violation in violations if isinstance(violations, list) else []:
            quota = violation.get("quotaId") if isinstance(violation, dict) else None
            if isinstance(quota, str) and "perday" in quota.replace("_", "").lower():
                daily = True
    return retry_after, daily


def _category(response: httpx2.Response) -> str:
    """http_<status>, plus Google's own error.status word when it is a safe token."""
    category = f"http_{response.status_code}"
    try:
        body = response.json()
        if isinstance(body, list) and body:  # Google sometimes wraps the error in a list
            body = body[0]
        status = body["error"]["status"]
    except (TypeError, ValueError, KeyError, IndexError):
        return category
    if isinstance(status, str) and _GOOGLE_STATUS.match(status):
        return f"{category}_{status}"
    return category
