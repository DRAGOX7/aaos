"""Model-independent immutable values for local inference."""

from dataclasses import dataclass
from typing import Literal

ChatRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single visible message in an inference conversation."""

    role: ChatRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in {"system", "user", "assistant"}:
            raise ValueError("role must be one of: system, user, assistant")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must not be blank")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """An ordered request for visible chat generation."""

    messages: tuple[ChatMessage, ...]
    max_output_tokens: int | None = None
    reasoning: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("messages must be a tuple")
        if not self.messages:
            raise ValueError("messages must not be empty")
        if not all(isinstance(message, ChatMessage) for message in self.messages):
            raise TypeError("messages must contain ChatMessage values")
        if self.max_output_tokens is not None and (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or self.max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be positive")
        if not isinstance(self.reasoning, bool):
            raise ValueError("reasoning must be a bool")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Validated token counts reported by an inference backend."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        token_counts = (self.input_tokens, self.output_tokens, self.total_tokens)
        if (
            any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in token_counts)
            or self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("token counts must be non-negative integers with an exact total")


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Visible assistant output and optional backend token usage."""

    content: str
    model_identifier: str
    usage: TokenUsage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must not be blank")
        if not isinstance(self.model_identifier, str) or not self.model_identifier.strip():
            raise ValueError("model_identifier must not be blank")
        if self.usage is not None and not isinstance(self.usage, TokenUsage):
            raise TypeError("usage must be a TokenUsage or None")


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    """Model inventory diagnostics without adapter-specific payloads."""

    reachable: bool
    available: bool
    configured_model: str
    available_models: tuple[str, ...]
    loaded_context_lengths: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reachable, bool):
            raise ValueError("reachable must be a bool")
        if not isinstance(self.available, bool):
            raise ValueError("available must be a bool")
        if not isinstance(self.configured_model, str) or not self.configured_model.strip():
            raise ValueError("configured_model must not be blank")
        if not isinstance(self.available_models, tuple):
            raise TypeError("available_models must be a tuple")
        if not all(isinstance(model, str) and model.strip() for model in self.available_models):
            raise ValueError("available_models must contain non-blank strings")
        if not isinstance(self.loaded_context_lengths, tuple):
            raise TypeError("loaded_context_lengths must be a tuple")
        if not self.available and self.loaded_context_lengths:
            raise ValueError("loaded_context_lengths must be empty when unavailable")
        if self.available and not self.reachable:
            raise ValueError("available model must be reachable")
        if self.available and self.configured_model not in self.available_models:
            raise ValueError("configured_model must be present when available")
        if self.available and not self.loaded_context_lengths:
            raise ValueError("available model requires loaded context lengths")
        if any(
            not isinstance(context_length, int)
            or isinstance(context_length, bool)
            or context_length <= 0
            for context_length in self.loaded_context_lengths
        ):
            raise ValueError("loaded context lengths must be positive integers")
