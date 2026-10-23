"""Optional ChatGPT-plan model gateway (Phase 11, slice 11D; the local model is the default).

Selecting this gateway sends prompts, which include question text and course-file
excerpts, to OpenAI on the owner's ChatGPT plan (Sign in with ChatGPT). Guard rails:

* a plain model call: no tools, functions, web search, MCP or agent settings, and never
  the Codex CLI; AAOS's own prompt is the only instruction sent;
* the endpoint is a fixed HTTPS constant, with no redirects and no proxies;
* the access token comes only from the credential store, sits in one request header,
  and appears in no telemetry, error message or repr; the gateway never sees the
  refresh token;
* the preview drops ``max_output_tokens`` and ``temperature``, so they are not sent:
  AAOS caps the visible output itself while reading the stream, and never uses a
  truncated reply;
* a reply counts only after ``response.completed``; a plan-limit error fails at once
  and is never retried or moved to other billing;
* AAOS still validates every reply with its own strict parsers, so dropping the JSON
  schema on a retry weakens nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2

from aaos.config import AppSettings

from .chatgpt_plan_credentials import ChatGPTCredentialStore, ChatGPTPlanUnavailable
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
from .lm_studio import _STRUCTURED_RESPONSE_FORMATS, _estimate_prompt_tokens
from .models import ChatRequest, ChatResponse, ModelAvailability, TokenUsage
from .telemetry import (
    LocalInferenceTelemetryEvent,
    TelemetrySink,
    emit_local_inference_telemetry,
    safe_error_code,
    safe_exception_type,
)

_LOGGER = logging.getLogger("aaos.inference.chatgpt_plan")

API_BASE_URL = "https://api.openai.com/v1/"
# Reasoning efforts to try, lowest first. The 2026-10-23 probe found that gpt-6.1-sol
# rejects "none" and "minimal" (unsupported_value, param reasoning.effort) and accepts
# "low"; a model that rejects every value here is asked without the setting.
REASONING_EFFORTS = ("none", "low")
# Waits before each retry of a short "unavailable" answer (HTTP 503).
_UNAVAILABLE_RETRY_DELAYS = (1.0, 3.0, 6.0)
# The hard stop while streaming: generous enough for Arabic text (about 2 bytes a
# character), tight enough that a runaway reply is cut off. The reported token count is
# the precise check once the reply completes.
_OUTPUT_BYTES_PER_TOKEN = 8
# OpenAI error codes and params are lowercase words; anything else (a token echoed back by
# a hostile server, for one) is never kept.
_ERROR_WORD = re.compile(r"^[a-z][a-z0-9_.]{0,79}$")
_MODEL_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PLAN_LIMIT_CODES = frozenset({"subscription_sharing_usage_limit_exceeded"})
_UNAVAILABLE_CODES = frozenset(
    {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}
)
_NOT_ELIGIBLE_CODES = frozenset(
    {
        "subscription_sharing_user_not_eligible",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
    }
)


class ChatGPTPlanHTTPError(InferenceBackendError):
    """A ChatGPT-plan failure that keeps its status and safe category (never a body)."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None,
        category: str,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.category = category
        # Which request field OpenAI rejected, when it said so (a safe word only).
        self.param = param


class _RequestPacer:
    """Optionally space requests out; process-wide, like the Gemini pacer."""

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


def _safe(word: object) -> str | None:
    return word if isinstance(word, str) and _ERROR_WORD.match(word) else None


def _slug(name: object) -> str | None:
    return name if isinstance(name, str) and _MODEL_SLUG.match(name) else None


def build_payload(
    model: str,
    request: ChatRequest,
    *,
    with_schema: bool,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """The Responses API body for ``request``: streamed, unstored, and without tools."""
    payload: dict[str, Any] = {
        "model": model,
        # System messages are rejected as such; they go as developer messages.
        "input": [
            {
                "role": "developer" if message.role == "system" else message.role,
                "content": message.content,
            }
            for message in request.messages
        ],
        "stream": True,
        "store": False,
    }
    if with_schema and request.structured_output == "json_schema":
        wrapper = _STRUCTURED_RESPONSE_FORMATS[request.structured_output_schema]["json_schema"]
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": wrapper["name"],
                "schema": wrapper["schema"],
                "strict": bool(wrapper.get("strict", True)),
            }
        }
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


async def _sse_events(response: httpx2.Response) -> AsyncIterator[dict[str, Any]]:
    """Server-sent events as JSON objects; the event name fills in a missing ``type``."""
    name: str | None = None
    data: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                text = "\n".join(data)
                data = []
                if text.strip() == "[DONE]":
                    name = None
                    continue
                try:
                    event = json.loads(text)
                except ValueError:
                    raise InferenceProtocolError("ChatGPT plan sent an unreadable event") from None
                if isinstance(event, dict):
                    event.setdefault("type", name)
                    yield event
            name = None
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        try:
            event = json.loads("\n".join(data))
        except ValueError:
            return
        if isinstance(event, dict):
            event.setdefault("type", name)
            yield event


def _output_text(response: object) -> str:
    """Text from a completed response's output items (when no deltas were streamed)."""
    if not isinstance(response, dict):
        return ""
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _usage(response: object) -> tuple[TokenUsage | None, int | None]:
    """Token usage, and the visible (non-reasoning) output token count, best effort."""
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (input_tokens, output_tokens)
    ):
        return None, None
    details = usage.get("output_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else 0
    if not isinstance(reasoning, int) or isinstance(reasoning, bool) or reasoning < 0:
        reasoning = 0
    assert isinstance(input_tokens, int) and isinstance(output_tokens, int)
    return (
        TokenUsage(input_tokens, output_tokens, input_tokens + output_tokens),
        max(output_tokens - reasoning, 0),
    )


def _error_code(body: bytes) -> tuple[str | None, str | None]:
    """OpenAI's ``error.code`` and ``error.param`` when they are safe words."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None, None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return None, None
    return _safe(error.get("code")), _safe(error.get("param"))


class ChatGPTPlanGateway:
    """Own one async client for the Responses API, authorized by the credential store."""

    def __init__(
        self,
        settings: AppSettings,
        transport: httpx2.AsyncBaseTransport,
        *,
        store: ChatGPTCredentialStore,
        diagnostic_sink: TelemetrySink | None = None,
        unavailable_retry_delays: tuple[float, ...] = _UNAVAILABLE_RETRY_DELAYS,
        on_failure: Callable[[str], None] | None = None,
        on_success: Callable[[], None] | None = None,
        min_interval_seconds: float = 0.0,
        owned_closers: tuple[Callable[[], Any], ...] = (),
    ) -> None:
        if settings.model_provider != "chatgpt_plan" or settings.chatgpt_plan_model is None:
            raise ValueError("settings must select the chatgpt_plan provider")
        self._settings = settings
        self._model = settings.chatgpt_plan_model
        self._store = store
        self._client = httpx2.AsyncClient(
            transport=transport,
            base_url=API_BASE_URL,
            timeout=settings.lm_studio_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._diagnostic_sink = diagnostic_sink or emit_local_inference_telemetry
        self._unavailable_retry_delays = unavailable_retry_delays
        self._on_failure = on_failure
        self._on_success = on_success
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._owned_closers = owned_closers
        self._closed = False
        # What the server rejected so far, so later requests do not repeat it: an index
        # into the reasoning efforts (len = send none), and schemas it refused.
        self._effort_index = 0
        self._rejected_schemas: set[str] = set()

    def __repr__(self) -> str:
        return f"ChatGPTPlanGateway(model={self._model!r})"

    async def check_availability(self) -> ModelAvailability:
        """List the plan's models and report whether the configured one is offered."""
        names = await _list_models(self._client, self._store)
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
        """Send one request and return the validated visible output."""
        if self._closed:
            raise InferenceError("ChatGPT plan gateway is closed") from None
        if request.reasoning:
            raise UnsupportedInferenceFeatureError("Reasoning is not supported")
        context_length = self._settings.lm_studio_context_length
        if request.max_output_tokens is not None:
            if request.max_output_tokens > context_length:
                raise ValueError("max_output_tokens exceeds the configured context length")
            if _estimate_prompt_tokens(request) + request.max_output_tokens > context_length:
                raise InferenceContextLimitError(
                    "ChatGPT plan request exceeds the configured context window"
                )
        schema = request.structured_output_schema
        structured = request.structured_output == "json_schema"
        # Retry with less, never more: each HTTP 400 removes exactly what it names (the
        # reasoning effort steps down, or this schema is dropped). A 400 that names
        # nothing drops the schema first, then the reasoning setting. Every step sends
        # less, so the loop ends; AAOS's own parsers still check every reply.
        while True:
            with_schema = structured and schema not in self._rejected_schemas
            effort = (
                REASONING_EFFORTS[self._effort_index]
                if self._effort_index < len(REASONING_EFFORTS)
                else None
            )
            payload = build_payload(
                self._model, request, with_schema=with_schema, reasoning_effort=effort
            )
            try:
                text, usage = await self._post_with_retry(payload, request)
                break
            except ChatGPTPlanHTTPError as error:
                if error.http_status != 400:
                    raise
                param = error.param or ""
                if param.startswith("reasoning") and effort is not None:
                    self._effort_index += 1
                elif param.startswith("text") and with_schema:
                    self._rejected_schemas.add(schema)
                elif with_schema:
                    self._rejected_schemas.add(schema)
                elif effort is not None:
                    self._effort_index += 1
                else:
                    raise
        self._report(self._on_success)
        return ChatResponse(content=text, model_identifier=self._model, usage=usage)

    async def _post_with_retry(
        self, payload: dict[str, Any], request: ChatRequest
    ) -> tuple[str, TokenUsage | None]:
        """One logical request: a refused token is refreshed once; 503s are waited out."""
        unavailable = 0
        refreshed = False
        token = await self._store.access_token()
        while True:
            await _PACER.wait(self._min_interval)
            try:
                return await self._stream(payload, token, request)
            except ChatGPTPlanHTTPError as error:
                if error.http_status == 401 and not refreshed:
                    refreshed = True
                    token = await self._store.force_refresh(rejected_access_token=token)
                    continue
                if error.http_status == 401:
                    raise ChatGPTPlanUnavailable("SIGN_IN_REQUIRED") from None
                if error.category == "RETRYABLE" and unavailable < len(
                    self._unavailable_retry_delays
                ):
                    await asyncio.sleep(self._unavailable_retry_delays[unavailable])
                    unavailable += 1
                    continue
                raise

    async def _stream(
        self, payload: dict[str, Any], token: str, request: ChatRequest
    ) -> tuple[str, TokenUsage | None]:
        if self._closed:
            raise InferenceError("ChatGPT plan gateway is closed") from None
        limit = request.request_timeout_seconds or self._settings.lm_studio_request_timeout_seconds
        cap_bytes = (
            request.max_output_tokens * _OUTPUT_BYTES_PER_TOKEN
            if request.max_output_tokens is not None
            else None
        )
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        chunks: list[str] = []
        size = [0]  # visible bytes so far, updated by _take_event
        completed: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(limit):
                async with self._client.stream(
                    "POST", "responses", json=payload, headers=headers
                ) as response:
                    if not response.is_success:
                        body = await response.aread()
                        raise self._http_error(response.status_code, body)
                    # The stream is read to its end (only a few bytes follow
                    # response.completed), so every reader finishes normally; aclosing
                    # closes the event reader here when an error ends it early.
                    async with contextlib.aclosing(_sse_events(response)) as events:
                        async for event in events:
                            if completed is None:
                                completed = self._take_event(event, chunks, size, cap_bytes)
        except (ChatGPTPlanHTTPError, InferenceProtocolError):
            raise
        except TimeoutError:
            raise self._fail(
                InferenceTimeoutError(f"ChatGPT plan request timed out for model '{self._model}'")
            ) from None
        except httpx2.TimeoutException:
            raise self._fail(
                InferenceTimeoutError(f"ChatGPT plan request timed out for model '{self._model}'")
            ) from None
        except httpx2.RequestError:
            raise self._fail(
                InferenceConnectionError(
                    f"ChatGPT plan connection failed for model '{self._model}'"
                )
            ) from None
        if completed is None:
            raise self._fail(
                InferenceConnectionError("ChatGPT plan stream ended before the reply completed"),
                detail="stream_cut_short",
            )
        text = "".join(chunks) or _output_text(completed)
        usage, visible_tokens = _usage(completed)
        if (
            request.max_output_tokens is not None
            and visible_tokens is not None
            and visible_tokens > request.max_output_tokens
        ):
            raise self._fail(
                InferenceProtocolError("ChatGPT plan reply exceeded the output cap"),
                detail="over_cap",
            )
        if cap_bytes is not None and len(text.encode("utf-8")) > cap_bytes:
            raise self._fail(
                InferenceProtocolError("ChatGPT plan reply exceeded the output cap"),
                detail="over_cap",
            )
        if not text.strip():
            raise self._fail(
                InferenceProtocolError("ChatGPT plan returned an empty reply"), detail="empty"
            )
        self._emit(
            LocalInferenceTelemetryEvent(
                operation="INFERENCE_REQUEST",
                model_identifier=self._model,
                model_role="answer",
                lifecycle_state="completed",
                http_status=200,
                backend_category="success",
            )
        )
        return text, usage

    def _take_event(
        self,
        event: dict[str, Any],
        chunks: list[str],
        size: list[int],
        cap_bytes: int | None,
    ) -> dict[str, Any] | None:
        """Use one stream event; returns the completed response when it arrives."""
        kind = event.get("type")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
                size[0] += len(delta.encode("utf-8"))
                if cap_bytes is not None and size[0] > cap_bytes:
                    raise self._fail(
                        InferenceProtocolError("ChatGPT plan reply exceeded the output cap"),
                        detail="over_cap",
                    )
        elif kind == "response.completed":
            raw = event.get("response")
            return raw if isinstance(raw, dict) else {}
        elif kind in {"response.failed", "error"}:
            raise self._event_error(event)
        elif kind == "response.incomplete":
            raise self._fail(
                InferenceProtocolError("ChatGPT plan reply was incomplete"), detail="incomplete"
            )
        return None

    def _http_error(self, status: int, body: bytes) -> InferenceError:
        code, param = _error_code(body)
        category = f"http_{status}" + (f"_{code}" if code else "")
        error: InferenceError
        if status == 401:
            error = ChatGPTPlanHTTPError(
                "ChatGPT plan refused the sign-in (status 401)", http_status=401, category=category
            )
        elif status == 403 and code in _NOT_ELIGIBLE_CODES:
            error = ChatGPTPlanUnavailable("NOT_ELIGIBLE")
        elif status == 404:
            error = ModelUnavailableError(f"ChatGPT plan model '{self._model}' is unavailable")
        elif code in _PLAN_LIMIT_CODES:
            error = ChatGPTPlanHTTPError(
                "ChatGPT plan usage limit reached; see chatgpt.com/settings/usage",
                http_status=status,
                category="PLAN_LIMIT_REACHED",
            )
        elif status == 503 or code in _UNAVAILABLE_CODES:
            error = ChatGPTPlanHTTPError(
                f"ChatGPT plan is unavailable right now (status {status})",
                http_status=status,
                category="RETRYABLE",
            )
        else:
            error = ChatGPTPlanHTTPError(
                f"ChatGPT plan request failed for model '{self._model}' (status {status})",
                http_status=status,
                category=category,
                param=param,
            )
        detail = f"param={param}" if param else None
        return self._fail(error, http_status=status, category=category, detail=detail)

    def _event_error(self, event: dict[str, Any]) -> InferenceError:
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else event.get("error")
        code = _safe(error.get("code")) if isinstance(error, dict) else _safe(event.get("code"))
        if code in _PLAN_LIMIT_CODES:
            failure = ChatGPTPlanHTTPError(
                "ChatGPT plan usage limit reached; see chatgpt.com/settings/usage",
                http_status=None,
                category="PLAN_LIMIT_REACHED",
            )
        else:
            failure = ChatGPTPlanHTTPError(
                "ChatGPT plan reply failed",
                http_status=None,
                category="RETRYABLE" if code in _UNAVAILABLE_CODES else f"failed_{code}",
            )
        return self._fail(failure, category=f"event_{code}")

    def _fail(
        self,
        error: InferenceError,
        *,
        http_status: int | None = None,
        category: str | None = None,
        detail: str | None = None,
    ) -> InferenceError:
        """Report a failure in safe words (telemetry, log, model panel) and return it."""
        error_code = (
            "INFERENCE_BACKEND_ERROR"
            if isinstance(error, ChatGPTPlanHTTPError)
            else safe_error_code(error)
        )
        shown = getattr(error, "category", None) or category or error_code.lower()
        self._emit(
            LocalInferenceTelemetryEvent(
                operation="INFERENCE_REQUEST",
                model_identifier=self._model,
                model_role="answer",
                lifecycle_state="failed:generation",
                exception_type=safe_exception_type(error),
                safe_error_code=error_code,
                http_status=http_status,
                backend_category=category,
            )
        )
        _LOGGER.warning(
            "ChatGPT plan request failed: model=%s http_status=%s category=%s error=%s detail=%s",
            self._model,
            http_status,
            shown,
            error_code,
            detail,
        )
        self._report(
            self._on_failure,
            f"ChatGPT plan generation failed: {shown}" + (f" ({detail})" if detail else ""),
        )
        return error

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
        for closer in self._owned_closers:
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                continue


async def _list_models(
    client: httpx2.AsyncClient, store: ChatGPTCredentialStore
) -> tuple[str, ...]:
    token = await store.access_token()
    try:
        response = await client.get("models", headers={"Authorization": f"Bearer {token}"})
    except httpx2.HTTPError:
        raise InferenceConnectionError("ChatGPT plan model list could not be reached") from None
    if not response.is_success:
        raise InferenceBackendError(
            f"ChatGPT plan model list failed (status {response.status_code})"
        )
    try:
        payload = response.json()
    except ValueError:
        raise InferenceProtocolError("ChatGPT plan returned an unreadable model list") from None
    names: list[str] = []
    entries = payload.get("models") if isinstance(payload, dict) else None
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("visibility", "list") != "list":
                continue
            slug = _slug(entry.get("slug"))
            if slug is not None and slug not in names:
                names.append(slug)
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        for entry in payload["data"]:
            slug = _slug(entry.get("id")) if isinstance(entry, dict) else None
            if slug is not None and slug not in names:
                names.append(slug)
    else:
        raise InferenceProtocolError("ChatGPT plan returned an unreadable model list")
    return tuple(names)


async def fetch_chatgpt_plan_model_names(
    store: ChatGPTCredentialStore, transport: httpx2.AsyncBaseTransport | None = None
) -> tuple[str, ...]:
    """The models the signed-in plan offers, in the server's order (names only)."""
    async with httpx2.AsyncClient(
        transport=transport or httpx2.AsyncHTTPTransport(),
        base_url=API_BASE_URL,
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return await _list_models(client, store)
