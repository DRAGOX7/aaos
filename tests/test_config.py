"""Tests for immutable local inference configuration."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from aaos.config import AppSettings


def test_inference_settings_have_safe_local_defaults() -> None:
    """The approved local profile must remain the immutable default."""
    settings = AppSettings()

    assert settings.lm_studio_base_url == "http://127.0.0.1:1234"
    assert settings.lm_studio_model == "qwen/qwen3.5-4b"
    assert settings.lm_studio_context_length == 8192
    assert settings.lm_studio_request_timeout_seconds == 30.0


def test_settings_are_frozen() -> None:
    """Configuration must not be mutable after construction."""
    settings = AppSettings()

    with pytest.raises(FrozenInstanceError):
        settings.lm_studio_model = "another-model"  # type: ignore[misc]


def test_base_url_is_normalized_without_trailing_slash() -> None:
    """A root trailing slash is removed for deterministic endpoint construction."""
    settings = AppSettings(lm_studio_base_url="http://localhost:1234/")

    assert settings.lm_studio_base_url == "http://localhost:1234"


@pytest.mark.parametrize(
    "base_url",
    (
        "https://localhost:1234",
        "http://192.168.1.10:1234",
        "http://user:password@localhost:1234",
        "http://localhost:1234/api",
        "http://localhost:1234?debug=true",
        "http://localhost:1234#fragment",
    ),
)
def test_invalid_lm_studio_base_url_is_rejected(base_url: str) -> None:
    """Only root HTTP loopback URLs are valid inference targets."""
    with pytest.raises(ValueError, match="lm_studio_base_url"):
        AppSettings(lm_studio_base_url=base_url)


@pytest.mark.parametrize("model_identifier", ("", "   "))
def test_blank_model_identifier_is_rejected(model_identifier: str) -> None:
    """The selected local model identifier cannot be blank."""
    with pytest.raises(ValueError, match="lm_studio_model"):
        AppSettings(lm_studio_model=model_identifier)


@pytest.mark.parametrize("model_identifier", (None, 123, True, []))
def test_non_string_model_identifier_is_rejected(model_identifier: object) -> None:
    """The model identifier must be a string before whitespace normalization."""
    with pytest.raises(ValueError, match="lm_studio_model"):
        AppSettings(lm_studio_model=model_identifier)  # type: ignore[arg-type]


def test_model_identifier_surrounding_whitespace_is_normalized() -> None:
    """Model identifiers are trimmed without altering their meaningful content."""
    settings = AppSettings(lm_studio_model="  qwen/qwen3.5-4b  ")

    assert settings.lm_studio_model == "qwen/qwen3.5-4b"


@pytest.mark.parametrize("context_length", (0, -1, True, False))
def test_invalid_context_length_is_rejected(context_length: int | bool) -> None:
    """Context length must be a positive integer rather than a boolean."""
    with pytest.raises(ValueError, match="lm_studio_context_length"):
        AppSettings(lm_studio_context_length=context_length)


@pytest.mark.parametrize("timeout_seconds", (0.0, -1.0, inf, nan, True, False))
def test_invalid_request_timeout_is_rejected(timeout_seconds: float | bool) -> None:
    """Request timeout must be finite and strictly positive."""
    with pytest.raises(ValueError, match="lm_studio_request_timeout_seconds"):
        AppSettings(lm_studio_request_timeout_seconds=timeout_seconds)


def test_calendar_timezone_is_unset_without_the_environment_key() -> None:
    from aaos.config import settings_from_environment

    assert settings_from_environment({}).calendar_timezone is None


@pytest.mark.parametrize("raw", ("Asia/Amman", "  Asia/Amman  "))
def test_calendar_timezone_is_loaded_from_the_environment(raw: str) -> None:
    from aaos.config import CALENDAR_TIMEZONE_ENVIRONMENT_KEY, settings_from_environment

    settings = settings_from_environment({CALENDAR_TIMEZONE_ENVIRONMENT_KEY: raw})

    assert CALENDAR_TIMEZONE_ENVIRONMENT_KEY == "AAOS_CALENDAR_TIMEZONE"
    assert settings.calendar_timezone == "Asia/Amman"


@pytest.mark.parametrize("raw", ("", "   ", "Mars/Olympus_Mons", "UTC+3"))
def test_invalid_configured_calendar_timezone_fails_closed(raw: str) -> None:
    """A set but unusable value must never fall back to UTC or to unset."""
    from aaos.config import settings_from_environment

    with pytest.raises(ValueError, match="AAOS_CALENDAR_TIMEZONE"):
        settings_from_environment({"AAOS_CALENDAR_TIMEZONE": raw})


def test_environment_settings_ignore_unrelated_keys_and_keep_defaults() -> None:
    from aaos.config import settings_from_environment

    environment = {"AAOS_CALENDAR_TIMEZONE": "Asia/Amman", "AAOS_LM_STUDIO_MODEL": "other"}
    settings = settings_from_environment(environment)

    assert settings == AppSettings(calendar_timezone="Asia/Amman")
    assert environment == {"AAOS_CALENDAR_TIMEZONE": "Asia/Amman", "AAOS_LM_STUDIO_MODEL": "other"}
