"""Choose the model gateway the settings ask for (local LM Studio unless opted in)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol

import httpx2

from aaos.config import GEMINI_API_KEY_ENVIRONMENT_KEY, AppSettings

from .gateway import InferenceError, ModelGateway

_LOGGER = logging.getLogger("aaos.inference")


class ClosableModelGateway(ModelGateway, Protocol):
    """A gateway that owns a client the caller must close."""

    async def aclose(self) -> None: ...


def active_model_identifier(settings: AppSettings) -> str:
    """The model that answers: the Gemini model when opted in, else the local one."""
    if settings.model_provider == "gemini" and settings.gemini_model is not None:
        return settings.gemini_model.removeprefix("models/")
    return settings.lm_studio_model


def warn_if_cloud_model(settings: AppSettings) -> None:
    """Say loudly, once per call site, that prompts will leave this computer."""
    if settings.model_provider == "gemini":
        _LOGGER.warning(
            "CLOUD MODEL ACTIVE: questions and course-file excerpts are sent to Google "
            "(Gemini model %s). Unset AAOS_MODEL_PROVIDER to return to the local model.",
            active_model_identifier(settings),
        )


class NoOpModelLifecycle:
    """A cloud model has nothing to load, unload or free."""

    def ensure_answer_model_unloaded(self) -> None:
        return None

    def ensure_answer_model_loaded(self) -> None:
        return None

    def close(self) -> None:
        return None


GEMINI_MIN_INTERVAL_ENVIRONMENT_KEY = "AAOS_GEMINI_MIN_INTERVAL_SECONDS"
_MAX_MIN_INTERVAL = 60.0


def _min_interval_from_environment() -> float:
    """Seconds to leave between Gemini requests (0 = no pacing); bad values mean none."""
    raw = os.environ.get(GEMINI_MIN_INTERVAL_ENVIRONMENT_KEY, "").strip()
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        return 0.0
    return min(max(value, 0.0), _MAX_MIN_INTERVAL) if value == value else 0.0


async def list_gemini_models() -> tuple[str, ...]:
    """The chat models the configured Gemini key can use; the key is read from the environment."""
    from .gemini import fetch_gemini_model_names

    api_key = os.environ.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip()
    if not api_key:
        raise InferenceError(f"{GEMINI_API_KEY_ENVIRONMENT_KEY} is not set")
    return await fetch_gemini_model_names(api_key)


def build_model_gateway(
    settings: AppSettings,
    *,
    on_failure: Callable[[str], None] | None = None,
    on_success: Callable[[], None] | None = None,
) -> ClosableModelGateway:
    """Build the gateway for ``settings``; the Gemini key is read here, from the environment."""
    if settings.model_provider == "gemini":
        from .gemini import GeminiGateway

        api_key = os.environ.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip()
        min_interval = _min_interval_from_environment()
        if not api_key:
            raise InferenceError(f"{GEMINI_API_KEY_ENVIRONMENT_KEY} is not set")
        warn_if_cloud_model(settings)
        return GeminiGateway(
            settings,
            httpx2.AsyncHTTPTransport(),
            api_key=api_key,
            on_failure=on_failure,
            on_success=on_success,
            min_interval_seconds=min_interval,
        )

    from .lm_studio import LMStudioGateway

    return LMStudioGateway(settings, httpx2.AsyncHTTPTransport())
