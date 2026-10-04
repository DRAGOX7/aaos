"""Choose the model gateway the settings ask for (local LM Studio unless opted in)."""

from __future__ import annotations

import logging
import os
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


def build_model_gateway(settings: AppSettings) -> ClosableModelGateway:
    """Build the gateway for ``settings``; the Gemini key is read here, from the environment."""
    if settings.model_provider == "gemini":
        from .gemini import GeminiGateway

        api_key = os.environ.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip()
        if not api_key:
            raise InferenceError(f"{GEMINI_API_KEY_ENVIRONMENT_KEY} is not set")
        warn_if_cloud_model(settings)
        return GeminiGateway(settings, httpx2.AsyncHTTPTransport(), api_key=api_key)

    from .lm_studio import LMStudioGateway

    return LMStudioGateway(settings, httpx2.AsyncHTTPTransport())
