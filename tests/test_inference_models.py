from dataclasses import FrozenInstanceError
from math import inf, nan
from typing import cast

import pytest

from aaos.inference.models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatRole,
    ModelAvailability,
    TokenUsage,
)


def test_chat_request_preserves_ordered_messages() -> None:
    messages = (
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi"),
    )

    assert ChatRequest(messages=messages).messages == messages


def test_chat_message_preserves_non_blank_content_verbatim() -> None:
    message = ChatMessage(role="user", content="  Keep these spaces.  ")

    assert message.content == "  Keep these spaces.  "


def test_chat_message_rejects_whitespace_only_content() -> None:
    with pytest.raises(ValueError, match="content must not be blank"):
        ChatMessage(role="user", content=" \t\n ")


def test_chat_message_rejects_invalid_role() -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        ChatMessage(role=cast(ChatRole, "tool"), content="Hello")


def test_chat_request_requires_an_immutable_message_tuple() -> None:
    messages = cast(tuple[ChatMessage, ...], [ChatMessage(role="user", content="Hello")])

    with pytest.raises(TypeError, match="messages must be a tuple"):
        ChatRequest(messages=messages)


def test_chat_request_is_frozen() -> None:
    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))

    with pytest.raises(FrozenInstanceError):
        request.reasoning = False  # type: ignore[misc]


def test_chat_request_rejects_empty_messages() -> None:
    with pytest.raises(ValueError, match="messages must not be empty"):
        ChatRequest(messages=())


@pytest.mark.parametrize("max_output_tokens", [0, -1, True])
def test_non_positive_or_boolean_output_limit_is_rejected(max_output_tokens: int) -> None:
    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            max_output_tokens=max_output_tokens,
        )


def test_chat_request_rejects_non_boolean_reasoning() -> None:
    with pytest.raises(ValueError, match="reasoning must be a bool"):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            reasoning=cast(bool, 1),
        )


@pytest.mark.parametrize("reasoning", [False, True])
def test_chat_request_accepts_boolean_reasoning(reasoning: bool) -> None:
    request = ChatRequest(
        messages=(ChatMessage(role="user", content="Hello"),),
        reasoning=reasoning,
    )

    assert request.reasoning is reasoning


@pytest.mark.parametrize("temperature", [-1.0, inf, nan, True])
def test_chat_request_rejects_invalid_temperature(temperature: float | bool) -> None:
    with pytest.raises(ValueError, match="temperature"):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            temperature=temperature,
        )


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, inf, nan, True])
def test_chat_request_rejects_invalid_request_timeout(timeout_seconds: float | bool) -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            request_timeout_seconds=timeout_seconds,
        )


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens", "total_tokens"),
    [
        (-1, 0, -1),
        (0, -1, -1),
        (1, 2, 4),
        (True, 0, 1),
        (0, True, 1),
        (0, 0, False),
    ],
)
def test_token_usage_rejects_invalid_counts(
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> None:
    with pytest.raises(ValueError, match="token counts"):
        TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


def test_token_usage_preserves_non_negative_exact_total() -> None:
    usage = TokenUsage(input_tokens=3, output_tokens=5, total_tokens=8)

    assert usage == TokenUsage(input_tokens=3, output_tokens=5, total_tokens=8)


@pytest.mark.parametrize(
    ("content", "model_identifier"),
    [("", "qwen/qwen3.5-4b"), ("Answer", " \t")],
)
def test_chat_response_rejects_blank_required_fields(content: str, model_identifier: str) -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        ChatResponse(content=content, model_identifier=model_identifier)


def test_model_availability_accepts_consistent_available_result() -> None:
    result = ModelAvailability(
        reachable=True,
        available=True,
        configured_model="qwen/qwen3.5-4b",
        available_models=("qwen/qwen3.5-4b", "other/model"),
        loaded_context_lengths=(8192,),
    )

    assert result.loaded_context_lengths == (8192,)


@pytest.mark.parametrize(
    ("reachable", "available_models", "loaded_context_lengths"),
    [
        (False, ("qwen/qwen3.5-4b",), (8192,)),
        (True, ("other/model",), (8192,)),
        (True, ("qwen/qwen3.5-4b",), ()),
    ],
)
def test_model_availability_rejects_inconsistent_available_result(
    reachable: bool,
    available_models: tuple[str, ...],
    loaded_context_lengths: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError):
        ModelAvailability(
            reachable=reachable,
            available=True,
            configured_model="qwen/qwen3.5-4b",
            available_models=available_models,
            loaded_context_lengths=loaded_context_lengths,
        )


@pytest.mark.parametrize("loaded_context_lengths", [(0,), (-1,), (True,)])
def test_model_availability_rejects_invalid_loaded_context_lengths(
    loaded_context_lengths: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="loaded context lengths"):
        ModelAvailability(
            reachable=True,
            available=True,
            configured_model="qwen/qwen3.5-4b",
            available_models=("qwen/qwen3.5-4b",),
            loaded_context_lengths=loaded_context_lengths,
        )


def test_unavailable_model_availability_rejects_loaded_context_lengths() -> None:
    with pytest.raises(ValueError, match="loaded_context_lengths must be empty"):
        ModelAvailability(
            reachable=True,
            available=False,
            configured_model="qwen/qwen3.5-4b",
            available_models=("qwen/qwen3.5-4b",),
            loaded_context_lengths=(8192,),
        )
