"""Non-secret application configuration for the AAOS foundation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Basic process metadata with safe local-development defaults."""

    application_name: str = "aaos"
    application_version: str = "0.1.0"
    environment_name: str = "development"
    lm_studio_base_url: str = "http://127.0.0.1:1234"
    lm_studio_model: str = "qwen/qwen3.5-4b"
    lm_studio_context_length: int = 8192
    lm_studio_request_timeout_seconds: float = 30.0
    local_approver_identity: str | None = None
    local_approver_context_id: str | None = None
    calendar_timezone: str | None = None

    def __post_init__(self) -> None:
        """Validate the fixed local inference profile without contacting LM Studio."""
        try:
            parsed_url = urlsplit(self.lm_studio_base_url)
            hostname = parsed_url.hostname
            _ = parsed_url.port
        except (TypeError, ValueError) as error:
            raise ValueError("lm_studio_base_url must be a valid local HTTP URL") from error

        if (
            parsed_url.scheme != "http"
            or hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("lm_studio_base_url must be a root HTTP loopback URL")

        if not isinstance(self.lm_studio_model, str):
            raise ValueError("lm_studio_model must be a non-blank string")

        model_identifier = self.lm_studio_model.strip()
        if not model_identifier:
            raise ValueError("lm_studio_model must not be blank")

        if (
            isinstance(self.lm_studio_context_length, bool)
            or not isinstance(self.lm_studio_context_length, int)
            or self.lm_studio_context_length <= 0
        ):
            raise ValueError("lm_studio_context_length must be a positive integer")

        timeout_seconds = self.lm_studio_request_timeout_seconds
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("lm_studio_request_timeout_seconds must be positive and finite")

        object.__setattr__(self, "lm_studio_base_url", self.lm_studio_base_url.rstrip("/"))
        object.__setattr__(self, "lm_studio_model", model_identifier)

        identity, context_id = self.local_approver_identity, self.local_approver_context_id
        if (identity is None) != (context_id is None):
            raise ValueError("local approver identity and context must be configured together")
        if identity is not None and (
            not _LOCAL_ID.fullmatch(identity)
            or context_id is None
            or not _LOCAL_ID.fullmatch(context_id)
        ):
            raise ValueError("local approver identity and context must be safe identifiers")
        if identity is not None and context_id is not None:
            # Validate the exact trusted value once at configuration load.
            from aaos.approval.contracts import TrustedApproverContext

            TrustedApproverContext(identity, context_id)

        if self.calendar_timezone is not None:
            if not isinstance(self.calendar_timezone, str) or not self.calendar_timezone:
                raise ValueError("calendar_timezone must be a valid IANA name or None")
            try:
                ZoneInfo(self.calendar_timezone)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("calendar_timezone must be a valid IANA name") from error


DEFAULT_SETTINGS = AppSettings()
