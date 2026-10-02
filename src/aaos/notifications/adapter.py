"""The destination boundary: the pipeline knows only this interface (spec §6.1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class DeliveryError(StrEnum):
    TOKEN_INVALID = "TOKEN_INVALID"
    CHAT_FORBIDDEN = "CHAT_FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    MESSAGE_INVALID = "MESSAGE_INVALID"
    NOT_LINKED = "NOT_LINKED"


# Errors that a later attempt cannot fix: the delivery fails at once.
PERMANENT_ERRORS = frozenset(
    {DeliveryError.TOKEN_INVALID, DeliveryError.CHAT_FORBIDDEN, DeliveryError.MESSAGE_INVALID}
)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: bool
    error: DeliveryError | None = None
    retry_after_seconds: int | None = None


class NotificationAdapter(Protocol):
    def send(self, html_message: str) -> DeliveryResult: ...
