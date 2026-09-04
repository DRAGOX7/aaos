"""Offline tests for LM Studio model-availability diagnostics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx2
import pytest

from aaos.config import AppSettings
from aaos.inference import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    InferenceBackendError,
    InferenceConnectionError,
    InferenceContextLimitError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelAvailability,
    ModelUnavailableError,
    StructuredOutputSchema,
    TokenUsage,
    UnsupportedInferenceFeatureError,
)
from aaos.inference.lm_studio import LMStudioGateway


class RecordingTransport(httpx2.AsyncBaseTransport):
    """Record requests and closes without contacting a network."""

    def __init__(self) -> None:
        self.request_count = 0
        self.close_count = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.request_count += 1
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "Visible"}}],
            },
        )

    async def aclose(self) -> None:
        self.close_count += 1


def _check_availability(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    settings: AppSettings | None = None,
) -> ModelAvailability:
    async def scenario() -> ModelAvailability:
        gateway = LMStudioGateway(settings or AppSettings(), httpx2.MockTransport(handler))
        try:
            return await gateway.check_availability()
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def _generate(
    request: ChatRequest,
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    settings: AppSettings | None = None,
) -> ChatResponse:
    async def scenario() -> ChatResponse:
        gateway = LMStudioGateway(settings or AppSettings(), httpx2.MockTransport(handler))
        try:
            return await gateway.generate(request)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def test_constructor_applies_the_required_private_client_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class ClientProbe:
        async def aclose(self) -> None:
            return None

    def client_factory(**kwargs: object) -> ClientProbe:
        captured.update(kwargs)
        return ClientProbe()

    monkeypatch.setattr(httpx2, "AsyncClient", client_factory)
    settings = AppSettings(
        lm_studio_base_url="http://127.0.0.1:4321",
        lm_studio_request_timeout_seconds=7.5,
    )
    transport = RecordingTransport()

    gateway = LMStudioGateway(settings, transport)
    asyncio.run(gateway.aclose())

    assert captured == {
        "transport": transport,
        "base_url": "http://127.0.0.1:4321",
        "timeout": 7.5,
        "follow_redirects": False,
        "trust_env": False,
    }


@pytest.mark.parametrize("operation", ("availability", "generation"))
def test_gateway_cleanup_is_idempotent_and_closed_use_fails_before_transport(
    operation: str,
) -> None:
    transport = RecordingTransport()

    async def scenario() -> None:
        gateway = LMStudioGateway(AppSettings(), transport)
        assert transport.request_count == 0
        assert transport.close_count == 0

        await gateway.aclose()
        assert transport.close_count == 1
        await gateway.aclose()
        assert transport.close_count == 1

        with pytest.raises(InferenceError, match="^LM Studio gateway is closed$"):
            if operation == "availability":
                await gateway.check_availability()
            else:
                request = ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))
                await gateway.generate(request)

        assert transport.request_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_request", ("oversized", "reasoning"))
def test_closed_generate_precedes_request_validation(invalid_request: str) -> None:
    transport = RecordingTransport()

    async def scenario() -> None:
        gateway = LMStudioGateway(AppSettings(), transport)
        await gateway.aclose()

        request = ChatRequest(
            messages=(ChatMessage(role="user", content="SECRET_PROMPT"),),
            max_output_tokens=8193 if invalid_request == "oversized" else None,
            reasoning=invalid_request == "reasoning",
        )

        with pytest.raises(InferenceError) as caught:
            await gateway.generate(request)

        assert str(caught.value) == "LM Studio gateway is closed"
        assert caught.value.__cause__ is None
        assert transport.request_count == 0
        assert transport.close_count == 1

    asyncio.run(scenario())


def test_generate_sends_one_direct_non_reasoning_request() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "Plain reply"}}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    request = ChatRequest(
        messages=(
            ChatMessage(role="system", content="Be concise."),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
            ChatMessage(role="user", content="Continue"),
        )
    )

    result = _generate(request, handler)

    assert result == ChatResponse(
        content="Plain reply",
        model_identifier="qwen/qwen3.5-4b",
        usage=TokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    assert [(item.method, item.url.path) for item in requests] == [("POST", "/v1/chat/completions")]
    assert json.loads(requests[0].content) == {
        "model": "qwen/qwen3.5-4b",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Continue"},
        ],
        "stream": False,
        "reasoning_effort": "none",
    }


def test_generate_serializes_the_owned_grounded_json_schema() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            },
        )

    _generate(
        ChatRequest(
            messages=(ChatMessage(role="user", content="Return the answer."),),
            max_output_tokens=2048,
            structured_output="json_schema",
        ),
        handler,
    )

    payload = json.loads(requests[0].content)
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_grounded_rag_answer_v1",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["ANSWER", "PARTIAL_ANSWER", "INSUFFICIENT_EVIDENCE"],
                    },
                    "answer_text": {"type": "string", "minLength": 1},
                    "language": {"type": "string", "enum": ["en", "ar"]},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "claim_id": {"type": "string", "minLength": 1},
                                "text": {"type": "string", "minLength": 1},
                                "evidence_ids": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                    "minItems": 1,
                                },
                            },
                            "required": ["claim_id", "text", "evidence_ids"],
                        },
                    },
                },
                "required": ["status", "answer_text", "language", "claims"],
            },
        },
    }


def test_generate_serializes_classifier_temperature_without_changing_default_timeout() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            },
        )

    _generate(
        ChatRequest(
            messages=(ChatMessage(role="user", content="Classify."),),
            max_output_tokens=256,
            temperature=0.0,
        ),
        handler,
    )

    payload = json.loads(requests[0].content)
    assert payload["temperature"] == 0.0


def test_generate_applies_request_specific_timeout() -> None:
    captured: dict[str, object] = {}

    class ClientProbe:
        async def request(self, *args: object, **kwargs: object) -> httpx2.Response:
            captured.update(kwargs)
            return httpx2.Response(
                200,
                json={
                    "model": "qwen/qwen3.5-4b",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )

        async def aclose(self) -> None:
            return None

    def client_factory(**_kwargs: object) -> ClientProbe:
        return ClientProbe()

    original_client = httpx2.AsyncClient
    try:
        httpx2.AsyncClient = client_factory  # type: ignore[assignment, misc]
        _generate(
            ChatRequest(
                messages=(ChatMessage(role="user", content="Answer."),),
                request_timeout_seconds=60.0,
            ),
            lambda _request: httpx2.Response(200),
        )
    finally:
        httpx2.AsyncClient = original_client  # type: ignore[misc]

    assert captured["timeout"] == 60.0


def test_generate_serializes_the_owned_semantic_coverage_json_schema() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            },
        )

    _generate(
        ChatRequest(
            messages=(ChatMessage(role="user", content="Classify coverage."),),
            max_output_tokens=256,
            structured_output="json_schema",
            structured_output_schema="semantic_coverage",
        ),
        handler,
    )

    payload = json.loads(requests[0].content)
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_semantic_coverage_v1",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["SUPPORTED", "PARTIAL", "UNSUPPORTED"],
                    },
                    "unsupported_aspects": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 4,
                    },
                },
                "required": ["status", "unsupported_aspects"],
            },
        },
    }


@pytest.mark.parametrize(
    ("schema", "name", "item_required"),
    (
        ("requirement_decomposition", "aaos_requirement_decomposition_v1", ["requirement_text"]),
        (
            "requirement_coverage",
            "aaos_requirement_coverage_v1",
            ["requirement_id", "support", "evidence_ids"],
        ),
    ),
)
def test_generate_serializes_requirement_json_schema_contract(
    schema: StructuredOutputSchema, name: str, item_required: list[str]
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            },
        )

    _generate(
        ChatRequest(
            messages=(ChatMessage(role="user", content="Classify requirements."),),
            max_output_tokens=512,
            structured_output="json_schema",
            structured_output_schema=schema,
        ),
        handler,
    )

    response_format = json.loads(requests[0].content)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == name
    assert response_format["json_schema"]["strict"] is True
    schema_body = response_format["json_schema"]["schema"]
    assert schema_body["additionalProperties"] is False
    assert schema_body["required"] == ["requirements"]
    assert schema_body["properties"]["requirements"]["items"]["required"] == item_required


def test_generate_serializes_single_requirement_span_schema() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": '{"spans":[]}'}}],
            },
        )

    _generate(
        ChatRequest(
            messages=(ChatMessage(role="user", content="Extract a quote."),),
            max_output_tokens=384,
            structured_output="json_schema",
            structured_output_schema="evidence_spans_single_requirement",
        ),
        handler,
    )

    response_format = json.loads(requests[0].content)["response_format"]
    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_evidence_spans_single_requirement_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "spans": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                "quote": {"type": "string", "minLength": 1, "maxLength": 400},
                            },
                            "required": ["evidence_id", "quote"],
                        },
                    }
                },
                "required": ["spans"],
            },
        },
    }


def test_generate_rejects_prompt_plus_completion_over_context_before_transport() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(500)

    request = ChatRequest(
        messages=(ChatMessage(role="user", content="x" * 100_000),),
        max_output_tokens=2048,
        structured_output="json_schema",
    )

    with pytest.raises(InferenceContextLimitError) as caught:
        _generate(request, handler)

    assert str(caught.value) == "LM Studio request exceeds the configured context window"
    assert requests == []


@pytest.mark.parametrize("reasoning_content", ("", None))
def test_generate_accepts_empty_lm_studio_reasoning_and_tool_metadata(
    reasoning_content: object,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-dizh74j354h9s2eh5yxjp8",
                "object": "chat.completion",
                "created": 1786594699,
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "AAOS ready",
                            "reasoning_content": reasoning_content,
                            "tool_calls": [],
                        },
                        "logprobs": None,
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 19,
                    "completion_tokens": 4,
                    "total_tokens": 23,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
                "stats": {},
                "system_fingerprint": "qwen/qwen3.5-4b",
            },
        )

    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    result = _generate(request, handler)

    assert result == ChatResponse(
        content="AAOS ready",
        model_identifier="qwen/qwen3.5-4b",
        usage=TokenUsage(input_tokens=19, output_tokens=4, total_tokens=23),
    )


def test_generate_preserves_optional_max_tokens() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "configured/custom-model",
                "choices": [{"message": {"role": "assistant", "content": "Done"}}],
            },
        )

    settings = AppSettings(
        lm_studio_model="configured/custom-model",
        lm_studio_context_length=512,
    )
    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Generate"),),
        max_output_tokens=137,
    )

    result = _generate(request, handler, settings=settings)

    assert result == ChatResponse(
        content="Done",
        model_identifier="configured/custom-model",
        usage=None,
    )
    assert json.loads(requests[0].content)["max_tokens"] == 137


def test_generate_accepts_output_limit_when_prompt_and_completion_fit_context() -> None:
    requests: list[httpx2.Request] = []
    message = "x" * 100

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "Accepted"}}],
            },
        )

    request = ChatRequest(
        messages=(ChatMessage(role="user", content=message),),
        max_output_tokens=8000,
    )

    _generate(request, handler)

    payload = json.loads(requests[0].content)
    assert payload["messages"] == [{"role": "user", "content": message}]
    assert payload["max_tokens"] == 8000


def test_generate_rejects_output_limit_above_context_before_transport() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(500)

    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Hello"),),
        max_output_tokens=8193,
    )

    with pytest.raises(
        ValueError,
        match="^max_output_tokens exceeds the configured context length$",
    ):
        _generate(request, handler)

    assert requests == []


def test_generate_rejects_enabled_reasoning_before_transport() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(500)

    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Hello"),),
        reasoning=True,
    )

    with pytest.raises(
        UnsupportedInferenceFeatureError,
        match="^Reasoning is not supported in Phase 0E$",
    ):
        _generate(request, handler)

    assert requests == []


@pytest.mark.parametrize(
    "response_payload",
    (
        {
            "model": "",
            "choices": [{"message": {"role": "assistant", "content": "Visible"}}],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [{"message": {"role": "assistant", "content": " "}}],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible",
                        "tool_calls": [{"id": "SECRET_TOOL_CALL"}],
                    }
                }
            ],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible",
                        "tool_calls": "SECRET_MALFORMED_TOOL_CALLS",
                    }
                }
            ],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible",
                        "reasoning_content": [],
                    }
                }
            ],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible",
                        "reasoning": "Hidden",
                    }
                }
            ],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible",
                        "reasoning_content": "Hidden",
                    }
                }
            ],
        },
        {
            "model": "qwen/qwen3.5-4b",
            "choices": [{"message": {"role": "assistant", "content": "Visible"}}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 99,
            },
        },
    ),
)
def test_generate_rejects_non_plain_or_malformed_response(
    response_payload: object,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=response_payload)

    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(
        InferenceProtocolError,
        match=("^LM Studio returned invalid data during generation for model 'qwen/qwen3.5-4b'$"),
    ) as caught:
        _generate(request, handler)

    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("backend_error", "expected_type", "expected_message"),
    (
        (
            {"error": {"code": "model_not_found", "message": "SECRET_BODY"}},
            ModelUnavailableError,
            "LM Studio model 'qwen/qwen3.5-4b' is unavailable during generation",
        ),
        (
            {"error": {"type": "model_not_found", "message": "SECRET_BODY"}},
            ModelUnavailableError,
            "LM Studio model 'qwen/qwen3.5-4b' is unavailable during generation",
        ),
        (
            {
                "error": {
                    "param": "reasoning_effort",
                    "message": "SECRET_BODY",
                }
            },
            UnsupportedInferenceFeatureError,
            (
                "LM Studio does not support required reasoning-off generation "
                "for model 'qwen/qwen3.5-4b'"
            ),
        ),
        (
            {
                "error": {
                    "code": "context_length_exceeded",
                    "message": "SECRET_BODY",
                }
            },
            InferenceBackendError,
            ("LM Studio backend failed during generation for model 'qwen/qwen3.5-4b' (status 400)"),
        ),
        (
            {"error": {"code": "SECRET_CODE", "message": "SECRET_BODY"}},
            InferenceBackendError,
            ("LM Studio backend failed during generation for model 'qwen/qwen3.5-4b' (status 400)"),
        ),
    ),
)
def test_generation_backend_errors_are_typed_without_sensitive_data(
    backend_error: dict[str, object],
    expected_type: type[InferenceError],
    expected_message: str,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            400,
            json=backend_error,
            headers={
                "set-cookie": "SECRET_COOKIE",
                "authorization": "SECRET_AUTHORIZATION",
            },
        )

    request = ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))

    with pytest.raises(expected_type) as caught:
        _generate(request, handler)

    assert str(caught.value) == expected_message
    for secret in (
        "SECRET_BODY",
        "SECRET_CODE",
        "SECRET_COOKIE",
        "SECRET_AUTHORIZATION",
        "SECRET_PROMPT",
    ):
        assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(requests) == 1
    assert json.loads(requests[0].content)["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    ("native_error", "expected_type", "expected_message"),
    (
        (
            httpx2.TimeoutException("SECRET_TIMEOUT"),
            InferenceTimeoutError,
            ("LM Studio request timed out during generation for model 'qwen/qwen3.5-4b'"),
        ),
        (
            httpx2.RequestError("SECRET_CONNECTION"),
            InferenceConnectionError,
            ("LM Studio connection failed during generation for model 'qwen/qwen3.5-4b'"),
        ),
    ),
)
def test_generation_transport_errors_are_typed_without_sensitive_data(
    native_error: httpx2.RequestError,
    expected_type: type[InferenceError],
    expected_message: str,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        native_error.request = request
        raise native_error

    request = ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))

    with pytest.raises(expected_type) as caught:
        _generate(request, handler)

    assert str(caught.value) == expected_message
    assert "SECRET_TIMEOUT" not in str(caught.value)
    assert "SECRET_CONNECTION" not in str(caught.value)
    assert "SECRET_PROMPT" not in str(caught.value)
    assert native_error.request is not None
    assert b"SECRET_PROMPT" in native_error.request.content
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "response",
    (
        httpx2.Response(
            200,
            content=b"SECRET_MALFORMED_JSON",
            headers={"set-cookie": "SECRET_COOKIE"},
        ),
        httpx2.Response(
            200,
            json={"model": "qwen/qwen3.5-4b", "private": "SECRET_BODY"},
            headers={"authorization": "SECRET_AUTHORIZATION"},
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": " "}}],
                "private": "SECRET_BODY",
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"content": "SECRET_MISSING_ROLE_CONTENT"}}],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "user", "content": "SECRET_USER_ROLE_CONTENT"}}],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {"message": {"role": "system", "content": "SECRET_SYSTEM_ROLE_CONTENT"}}
                ],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": 7, "content": "SECRET_INVALID_ROLE_CONTENT"}}],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Visible",
                            "reasoning_content": "SECRET_REASONING",
                        }
                    }
                ],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Visible",
                            "tool_calls": [{"secret": "SECRET_TOOL"}],
                        }
                    }
                ],
            },
        ),
        httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "Visible"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": "SECRET_USAGE",
                },
            },
        ),
    ),
)
def test_generation_malformed_success_is_redacted_protocol_error(
    response: httpx2.Response,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return response

    request = ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))

    with pytest.raises(InferenceProtocolError) as caught:
        _generate(request, handler)

    assert str(caught.value) == (
        "LM Studio returned invalid data during generation for model 'qwen/qwen3.5-4b'"
    )
    for secret in (
        "SECRET_MALFORMED_JSON",
        "SECRET_BODY",
        "SECRET_COOKIE",
        "SECRET_AUTHORIZATION",
        "SECRET_MISSING_ROLE_CONTENT",
        "SECRET_USER_ROLE_CONTENT",
        "SECRET_SYSTEM_ROLE_CONTENT",
        "SECRET_INVALID_ROLE_CONTENT",
        "SECRET_REASONING",
        "SECRET_TOOL",
        "SECRET_USAGE",
        "SECRET_PROMPT",
    ):
        assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True


def test_availability_matches_exact_configured_model() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b",
                        "type": "llm",
                        "loaded_instances": [
                            {
                                "id": "qwen/qwen3.5-4b:1",
                                "config": {"context_length": 8192},
                            },
                            {
                                "id": "qwen/qwen3.5-4b:2",
                                "config": {"context_length": 4096},
                            },
                        ],
                        "capabilities": {
                            "reasoning": {
                                "allowed_options": ["off", "on"],
                                "default": "on",
                            }
                        },
                    },
                    {
                        "key": "another/downloaded-model",
                        "type": "llm",
                        "loaded_instances": [],
                    },
                ]
            },
        )

    result = _check_availability(handler)

    assert result == ModelAvailability(
        reachable=True,
        available=True,
        configured_model="qwen/qwen3.5-4b",
        available_models=("qwen/qwen3.5-4b", "another/downloaded-model"),
        loaded_context_lengths=(8192, 4096),
    )
    assert [(request.method, request.url.path) for request in seen] == [("GET", "/api/v1/models")]


def test_downloaded_but_unloaded_model_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b",
                        "type": "llm",
                        "loaded_instances": [],
                    }
                ]
            },
        )

    result = _check_availability(handler)

    assert result == ModelAvailability(
        reachable=True,
        available=False,
        configured_model="qwen/qwen3.5-4b",
        available_models=("qwen/qwen3.5-4b",),
        loaded_context_lengths=(),
    )


def test_absent_model_is_unavailable_without_prefix_matching() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b-extra",
                        "type": "llm",
                        "loaded_instances": [{"config": {"context_length": 16384}}],
                    }
                ]
            },
        )

    result = _check_availability(handler)

    assert result.reachable is True
    assert result.available is False
    assert result.available_models == ("qwen/qwen3.5-4b-extra",)
    assert result.loaded_context_lengths == ()


def test_matching_loaded_non_llm_model_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b",
                        "type": "embedding",
                        "loaded_instances": [{"config": {"context_length": 8192}}],
                    }
                ]
            },
        )

    result = _check_availability(handler)

    assert result.reachable is True
    assert result.available is False
    assert result.available_models == ("qwen/qwen3.5-4b",)
    assert result.loaded_context_lengths == ()


def test_empty_inventory_is_reachable_but_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"models": []})

    result = _check_availability(handler)

    assert result == ModelAvailability(
        reachable=True,
        available=False,
        configured_model="qwen/qwen3.5-4b",
        available_models=(),
        loaded_context_lengths=(),
    )


@pytest.mark.parametrize(
    "payload",
    (
        None,
        [],
        {},
        {"models": {}},
        {"models": [None]},
        {"models": [{"key": "", "type": "llm", "loaded_instances": []}]},
        {"models": [{"key": "valid/model", "type": "unsupported", "loaded_instances": []}]},
        {"models": [{"key": "valid/model", "type": "llm", "loaded_instances": None}]},
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [{}],
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [{"config": {"context_length": True}}],
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [{"config": {"context_length": 0}}],
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [],
                    "capabilities": "reasoning",
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [],
                    "capabilities": {"reasoning": []},
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [],
                    "capabilities": {"reasoning": {"allowed_options": "off", "default": "off"}},
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [],
                    "capabilities": {
                        "reasoning": {"allowed_options": ["off", 1], "default": "off"}
                    },
                }
            ]
        },
        {
            "models": [
                {
                    "key": "valid/model",
                    "type": "llm",
                    "loaded_instances": [],
                    "capabilities": {"reasoning": {"allowed_options": ["off"], "default": "on"}},
                }
            ]
        },
    ),
)
def test_malformed_availability_payload_raises_protocol_error(payload: object) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    with pytest.raises(
        InferenceProtocolError,
        match=("^LM Studio returned invalid data during availability for model 'qwen/qwen3.5-4b'$"),
    ) as caught:
        _check_availability(handler)

    assert caught.value.__cause__ is None


def test_invalid_json_availability_response_raises_protocol_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"SECRET_INVALID_JSON")

    with pytest.raises(InferenceProtocolError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio returned invalid data during availability for model 'qwen/qwen3.5-4b'"
    )
    assert "SECRET_INVALID_JSON" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True


def test_non_success_availability_response_raises_safe_backend_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, text="SECRET_BACKEND_RESPONSE")

    with pytest.raises(InferenceBackendError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio backend failed during availability for model 'qwen/qwen3.5-4b' (status 503)"
    )
    assert "SECRET_BACKEND_RESPONSE" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_availability_timeout_is_translated_without_native_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("SECRET_TIMEOUT", request=request)

    with pytest.raises(InferenceTimeoutError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio request timed out during availability for model 'qwen/qwen3.5-4b'"
    )
    assert "SECRET_TIMEOUT" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_availability_request_error_is_translated_without_native_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RequestError("SECRET_CONNECTION", request=request)

    with pytest.raises(InferenceConnectionError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio connection failed during availability for model 'qwen/qwen3.5-4b'"
    )
    assert "SECRET_CONNECTION" not in str(caught.value)
    assert caught.value.__cause__ is None
