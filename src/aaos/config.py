"""Non-secret application configuration for the AAOS foundation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


MODEL_PROVIDERS = ("lm_studio", "gemini")


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
    # Which model answers. "lm_studio" (default) is fully local. "gemini" is an
    # explicit opt-in that sends prompts, including course excerpts, to Google; its
    # API key is never a setting: it is read from the environment by the gateway only.
    model_provider: str = "lm_studio"
    gemini_model: str | None = None
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

        if self.model_provider not in MODEL_PROVIDERS:
            raise ValueError(f"model_provider must be one of {', '.join(MODEL_PROVIDERS)}")
        if self.model_provider == "gemini":
            if not isinstance(self.gemini_model, str) or not self.gemini_model.strip():
                raise ValueError("gemini_model must be set when model_provider is gemini")
            object.__setattr__(self, "gemini_model", self.gemini_model.strip())
        elif self.gemini_model is not None:
            raise ValueError("gemini_model is only valid when model_provider is gemini")

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


CALENDAR_TIMEZONE_ENVIRONMENT_KEY = "AAOS_CALENDAR_TIMEZONE"
MODEL_PROVIDER_ENVIRONMENT_KEY = "AAOS_MODEL_PROVIDER"
GEMINI_MODEL_ENVIRONMENT_KEY = "AAOS_GEMINI_MODEL"
GEMINI_API_KEY_ENVIRONMENT_KEY = "GEMINI_API_KEY"


def settings_from_environment(environment: Mapping[str, str]) -> AppSettings:
    """Build settings from the explicit AAOS environment keys.

    Configurable: the calendar timezone (when the key is absent the timezone stays
    unset, so timezone-dependent features refuse; a key that is set but blank or not
    a valid IANA name fails closed instead of assuming UTC) and the model provider.
    The provider defaults to the local LM Studio model. Choosing ``gemini`` is an
    explicit opt-in that needs ``AAOS_GEMINI_MODEL`` and a ``GEMINI_API_KEY`` that
    is present in the environment; the key itself is never stored in the settings.
    """
    values: dict[str, object] = {}

    raw_timezone = environment.get(CALENDAR_TIMEZONE_ENVIRONMENT_KEY)
    if raw_timezone is not None:
        timezone_name = raw_timezone.strip()
        if not timezone_name:
            raise ValueError(
                f"{CALENDAR_TIMEZONE_ENVIRONMENT_KEY} must be a valid IANA timezone name when set"
            )
        values["calendar_timezone"] = timezone_name

    raw_provider = environment.get(MODEL_PROVIDER_ENVIRONMENT_KEY)
    if raw_provider is not None and raw_provider.strip():
        provider = raw_provider.strip().lower()
        if provider not in MODEL_PROVIDERS:
            raise ValueError(
                f"{MODEL_PROVIDER_ENVIRONMENT_KEY} must be one of {', '.join(MODEL_PROVIDERS)}"
            )
        values["model_provider"] = provider
        if provider == "gemini":
            if not environment.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip():
                raise ValueError(
                    f"{GEMINI_API_KEY_ENVIRONMENT_KEY} must be set in the environment "
                    f"when {MODEL_PROVIDER_ENVIRONMENT_KEY}=gemini"
                )
            values["gemini_model"] = environment.get(GEMINI_MODEL_ENVIRONMENT_KEY, "")

    try:
        return AppSettings(**values)  # type: ignore[arg-type]
    except ValueError as error:
        if "calendar_timezone" in values and "calendar_timezone" in str(error):
            raise ValueError(
                f"{CALENDAR_TIMEZONE_ENVIRONMENT_KEY} must be a valid IANA timezone name when set"
            ) from error
        if "gemini_model" in str(error):
            raise ValueError(
                f"{GEMINI_MODEL_ENVIRONMENT_KEY} must name a Gemini model when "
                f"{MODEL_PROVIDER_ENVIRONMENT_KEY}=gemini"
            ) from error
        raise


DEFAULT_SETTINGS = settings_from_environment(os.environ)
