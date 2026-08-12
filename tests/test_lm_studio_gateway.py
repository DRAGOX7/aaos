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
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelAvailability,
    TokenUsage,
    UnsupportedInferenceFeatureError,
)
from aaos.inference.lm_studio import LMStudioGateway


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


def test_generate_sends_one_direct_non_reasoning_request() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {"message": {"role": "assistant", "content": "Plain reply"}}
                ],
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
    assert [(item.method, item.url.path) for item in requests] == [
        ("POST", "/v1/chat/completions")
    ]
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


def test_generate_preserves_optional_max_tokens() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "configured/custom-model",
                "choices": [
                    {"message": {"role": "assistant", "content": "Done"}}
                ],
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


def test_generate_accepts_context_length_output_limit_without_inspecting_input() -> None:
    requests: list[httpx2.Request] = []
    long_message = "x" * 50_000

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [
                    {"message": {"role": "assistant", "content": "Accepted"}}
                ],
            },
        )

    request = ChatRequest(
        messages=(ChatMessage(role="user", content=long_message),),
        max_output_tokens=8192,
    )

    _generate(request, handler)

    payload = json.loads(requests[0].content)
    assert payload["messages"] == [{"role": "user", "content": long_message}]
    assert payload["max_tokens"] == 8192


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

    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))
    object.__setattr__(request, "reasoning", True)

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
            "choices": [
                {"message": {"role": "assistant", "content": "Visible"}}
            ],
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
                        "tool_calls": [],
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
            "choices": [
                {"message": {"role": "assistant", "content": "Visible"}}
            ],
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
        match=(
            "^LM Studio returned invalid data during generation for model "
            "'qwen/qwen3.5-4b'$"
        ),
    ) as caught:
        _generate(request, handler)

    assert caught.value.__cause__ is None


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
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/api/v1/models")
    ]


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
                        "loaded_instances": [
                            {"config": {"context_length": 16384}}
                        ],
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
                        "loaded_instances": [
                            {"config": {"context_length": 8192}}
                        ],
                    }
                ]
            },
        )

    result = _check_availability(handler)

    assert result.reachable is True
    assert result.available is False
    assert result.available_models == ("qwen/qwen3.5-4b",)
    assert result.loaded_context_lengths == ()


@pytest.mark.parametrize(
    "payload",
    (
        None,
        [],
        {},
        {"models": {}},
        {"models": [None]},
        {"models": [{"key": "", "type": "llm", "loaded_instances": []}]},
        {
            "models": [
                {"key": "valid/model", "type": "unsupported", "loaded_instances": []}
            ]
        },
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
                    "capabilities": {
                        "reasoning": {"allowed_options": "off", "default": "off"}
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
                    "capabilities": {
                        "reasoning": {"allowed_options": ["off"], "default": "on"}
                    },
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
        match=(
            "^LM Studio returned invalid data during availability for model "
            "'qwen/qwen3.5-4b'$"
        ),
    ) as caught:
        _check_availability(handler)

    assert caught.value.__cause__ is None


def test_invalid_json_availability_response_raises_protocol_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"SECRET_INVALID_JSON")

    with pytest.raises(InferenceProtocolError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio returned invalid data during availability for model "
        "'qwen/qwen3.5-4b'"
    )
    assert "SECRET_INVALID_JSON" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_non_success_availability_response_raises_safe_backend_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, text="SECRET_BACKEND_RESPONSE")

    with pytest.raises(InferenceBackendError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio backend failed during availability for model "
        "'qwen/qwen3.5-4b' (status 503)"
    )
    assert "SECRET_BACKEND_RESPONSE" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_availability_timeout_is_translated_without_native_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("SECRET_TIMEOUT", request=request)

    with pytest.raises(InferenceTimeoutError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio request timed out during availability for model "
        "'qwen/qwen3.5-4b'"
    )
    assert "SECRET_TIMEOUT" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_availability_request_error_is_translated_without_native_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RequestError("SECRET_CONNECTION", request=request)

    with pytest.raises(InferenceConnectionError) as caught:
        _check_availability(handler)

    assert str(caught.value) == (
        "LM Studio connection failed during availability for model "
        "'qwen/qwen3.5-4b'"
    )
    assert "SECRET_CONNECTION" not in str(caught.value)
    assert caught.value.__cause__ is None
