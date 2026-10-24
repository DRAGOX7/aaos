"""Choose the model gateway the settings ask for (local LM Studio unless opted in)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

import httpx2

from aaos.config import GEMINI_API_KEY_ENVIRONMENT_KEY, AppSettings

from .gateway import InferenceError, ModelGateway
from .models import TokenUsage

_LOGGER = logging.getLogger("aaos.inference")


class ClosableModelGateway(ModelGateway, Protocol):
    """A gateway that owns a client the caller must close."""

    async def aclose(self) -> None: ...


def active_model_identifier(settings: AppSettings) -> str:
    """The model that answers: the opted-in cloud model, else the local one."""
    if settings.model_provider == "gemini" and settings.gemini_model is not None:
        return settings.gemini_model.removeprefix("models/")
    if settings.model_provider == "chatgpt_plan" and settings.chatgpt_plan_model is not None:
        return settings.chatgpt_plan_model
    return settings.lm_studio_model


def warn_if_cloud_model(settings: AppSettings) -> None:
    """Say loudly, once per call site, that prompts will leave this computer."""
    if settings.model_provider == "gemini":
        _LOGGER.warning(
            "CLOUD MODEL ACTIVE: questions and course-file excerpts are sent to Google "
            "(Gemini model %s). Unset AAOS_MODEL_PROVIDER to return to the local model.",
            active_model_identifier(settings),
        )
    elif settings.model_provider == "chatgpt_plan":
        _LOGGER.warning(
            "CLOUD MODEL ACTIVE: questions and course-file excerpts are sent to OpenAI on "
            "your ChatGPT plan (model %s). Unset AAOS_MODEL_PROVIDER to return to the "
            "local model.",
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
CHATGPT_PLAN_MIN_INTERVAL_ENVIRONMENT_KEY = "AAOS_CHATGPT_PLAN_MIN_INTERVAL_SECONDS"
_MAX_MIN_INTERVAL = 60.0


def _min_interval_from_environment(key: str = GEMINI_MIN_INTERVAL_ENVIRONMENT_KEY) -> float:
    """Seconds to leave between cloud requests (0 = no pacing); bad values mean none."""
    raw = os.environ.get(key, "").strip()
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


def _chatgpt_plan_store_and_auth() -> tuple[Any, Any]:
    """The credential store, refreshing through its own auth client (closed with it)."""
    from .chatgpt_plan_credentials import ChatGPTCredentialStore, credential_path_from_environment
    from .chatgpt_plan_signin import AuthClient

    store = ChatGPTCredentialStore(credential_path_from_environment())
    auth = AuthClient()
    store.set_refresher(auth.refresh)
    return store, auth


async def list_chatgpt_plan_models() -> tuple[str, ...]:
    """The models the signed-in ChatGPT plan offers; the sign-in is read from its file."""
    from .chatgpt_plan import fetch_chatgpt_plan_model_names

    store, auth = _chatgpt_plan_store_and_auth()
    try:
        return await fetch_chatgpt_plan_model_names(store)
    finally:
        await auth.aclose()


def chatgpt_plan_sign_in_state() -> str:
    """The sign-in state from the credential file alone (no network call)."""
    from .chatgpt_plan_credentials import (
        ChatGPTCredentialStore,
        ChatGPTPlanUnavailable,
        credential_path_from_environment,
    )

    try:
        return ChatGPTCredentialStore(credential_path_from_environment()).state()[0]
    except ChatGPTPlanUnavailable:
        return "NOT_CONFIGURED"


def build_model_gateway(
    settings: AppSettings,
    *,
    on_failure: Callable[[str], None] | None = None,
    on_success: Callable[[], None] | None = None,
    on_chatgpt_plan_usage: Callable[[TokenUsage | None], None] | None = None,
) -> ClosableModelGateway:
    """Build the gateway for ``settings``; cloud credentials are read here, never stored."""
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

    if settings.model_provider == "chatgpt_plan":
        from .chatgpt_plan import ChatGPTPlanGateway

        store, auth = _chatgpt_plan_store_and_auth()
        warn_if_cloud_model(settings)
        return ChatGPTPlanGateway(
            settings,
            httpx2.AsyncHTTPTransport(),
            store=store,
            on_failure=on_failure,
            on_success=on_success,
            on_usage=on_chatgpt_plan_usage,
            min_interval_seconds=_min_interval_from_environment(
                CHATGPT_PLAN_MIN_INTERVAL_ENVIRONMENT_KEY
            ),
            owned_closers=(auth.aclose,),
        )

    from .lm_studio import LMStudioGateway

    return LMStudioGateway(settings, httpx2.AsyncHTTPTransport())
