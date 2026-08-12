"""Offline tests for LM Studio model-availability diagnostics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx2
import pytest

from aaos.config import AppSettings
from aaos.inference import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelAvailability,
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
