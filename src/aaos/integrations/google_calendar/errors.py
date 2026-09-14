from __future__ import annotations

import unicodedata
from enum import StrEnum

_SAFE_REASON_LIMIT = 256


class GoogleProviderErrorKind(StrEnum):
    AUTHENTICATION_REJECTED = "authentication_rejected"
    PERMISSION_REJECTED = "permission_rejected"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    MALFORMED_RESPONSE = "malformed_response"


class GoogleProviderError(RuntimeError):
    """Closed provider failure carrying only a typed kind and safe reason."""

    def __init__(self, kind: GoogleProviderErrorKind, safe_reason: str) -> None:
        if not isinstance(kind, GoogleProviderErrorKind):
            raise TypeError("kind must be a GoogleProviderErrorKind")
        if not isinstance(safe_reason, str):
            raise TypeError("safe_reason must be a string")
        if (
            not safe_reason
            or len(safe_reason) > _SAFE_REASON_LIMIT
            or unicodedata.normalize("NFKC", safe_reason) != safe_reason
            or any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in safe_reason
            )
        ):
            raise ValueError("safe_reason is invalid or exceeds its limit")
        self.kind = kind
        self.safe_reason = safe_reason
        super().__init__(safe_reason)


__all__ = ["GoogleProviderError", "GoogleProviderErrorKind"]
