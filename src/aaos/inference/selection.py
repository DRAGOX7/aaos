"""Which model answers right now, and the one safe way to change it at runtime.

The choice is process memory only: a restart returns to whatever the environment
selected (the local model unless the owner opted in), so the safe default always wins.
The Gemini API key is never part of this state; readiness only records whether it is
present in the environment, and the gateway reads it itself when it is built.

The ChatGPT plan works the same way: its sign-in lives in an owner-only credential file
that this module never reads itself; readiness comes from a state callback that only
looks at that file (no network), and the model list from a lister.

Switching refuses to run while a question is in flight, sends nothing to a cloud
provider without an explicit confirmation (also when moving from one cloud provider to
the other, since the data then goes to a different company), and asks every holder of
a model-bound component (the assistant, the RAG runtime) to drop it so it is rebuilt
for the new model.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Literal

from aaos.config import (
    CHATGPT_PLAN_MODEL_ENVIRONMENT_KEY,
    CLOUD_MODEL_PROVIDERS,
    GEMINI_API_KEY_ENVIRONMENT_KEY,
    GEMINI_MODEL_ENVIRONMENT_KEY,
    MODEL_PROVIDERS,
    AppSettings,
)

GeminiBlocker = Literal["GEMINI_API_KEY_MISSING", "GEMINI_MODEL_MISSING"]
ChatGPTPlanBlocker = Literal[
    "CHATGPT_PLAN_SIGN_IN_REQUIRED",
    "CHATGPT_PLAN_USAGE_NOT_GRANTED",
    "CHATGPT_PLAN_MODEL_MISSING",
]
SelectionErrorCode = Literal[
    "INVALID_PROVIDER",
    "CLOUD_CONFIRMATION_REQUIRED",
    "GEMINI_NOT_READY",
    "CHATGPT_PLAN_NOT_READY",
    "MODEL_BUSY",
    "MODEL_SWITCH_FAILED",
    "INVALID_MODEL",
    "MODEL_LIST_FAILED",
]


class ModelSelectionError(Exception):
    """A refused or failed model switch; carries a browser-safe code only."""

    def __init__(self, code: SelectionErrorCode) -> None:
        super().__init__(code)
        self.code: SelectionErrorCode = code


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """What is answering, and whether the other option could be switched to."""

    provider: str
    model: str
    cloud: bool
    local_model: str
    gemini_model: str | None
    gemini_ready: bool
    gemini_blocker: GeminiBlocker | None
    # The last model problem, as safe words only (never a prompt, reply or key).
    last_failure: str | None = None
    chatgpt_plan_model: str | None = None
    chatgpt_plan_ready: bool = False
    chatgpt_plan_blocker: ChatGPTPlanBlocker | None = None


Listener = Callable[[], Awaitable[None]]
GeminiLister = Callable[[], Awaitable[tuple[str, ...]]]
ModelLister = GeminiLister
SignInState = Callable[[], str]

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_MODEL_LIST_TTL_SECONDS = 300.0


def _normalize_model(name: str) -> str | None:
    cleaned = name.strip().removeprefix("models/")
    return cleaned if _MODEL_NAME.match(cleaned) else None


class ModelSelection:
    """Shared, in-memory model choice for one running AAOS process."""

    def __init__(
        self,
        settings: AppSettings,
        environment: Mapping[str, str] | None = None,
        gemini_lister: GeminiLister | None = None,
        chatgpt_plan_lister: ModelLister | None = None,
        chatgpt_plan_state: SignInState | None = None,
    ) -> None:
        self._base = settings
        self._gemini_lister = gemini_lister
        self._gemini_override: str | None = None
        self._models_cache: tuple[float, tuple[str, ...]] | None = None
        self._chatgpt_plan_lister = chatgpt_plan_lister
        self._chatgpt_plan_state = chatgpt_plan_state
        self._chatgpt_plan_override: str | None = None
        self._chatgpt_plan_cache: tuple[float, tuple[str, ...]] | None = None
        self._environment = environment if environment is not None else os.environ
        self._provider = settings.model_provider
        self._listeners: list[Listener] = []
        self._active = 0
        self._last_failure: str | None = None
        self._select_lock = asyncio.Lock()
        self._gate = asyncio.Event()
        self._gate.set()

    # --- reading ------------------------------------------------------------------------

    def _gemini_model(self) -> str | None:
        configured = (
            self._gemini_override
            or self._base.gemini_model
            or self._environment.get(GEMINI_MODEL_ENVIRONMENT_KEY)
        )
        if configured is None or not configured.strip():
            return None
        return configured.strip().removeprefix("models/")

    def _chatgpt_plan_model(self) -> str | None:
        configured = (
            self._chatgpt_plan_override
            or self._base.chatgpt_plan_model
            or self._environment.get(CHATGPT_PLAN_MODEL_ENVIRONMENT_KEY)
        )
        if configured is None or not configured.strip():
            return None
        return configured.strip()

    def _chatgpt_plan_sign_in(self) -> str:
        if self._chatgpt_plan_state is None:
            return "NOT_CONFIGURED"
        try:
            return self._chatgpt_plan_state()
        except Exception:
            return "NOT_CONFIGURED"

    def _chatgpt_plan_blocker(self) -> ChatGPTPlanBlocker | None:
        sign_in = self._chatgpt_plan_sign_in()
        if sign_in == "PLAN_USAGE_NOT_GRANTED":
            return "CHATGPT_PLAN_USAGE_NOT_GRANTED"
        if sign_in != "SIGNED_IN":
            return "CHATGPT_PLAN_SIGN_IN_REQUIRED"
        if self._chatgpt_plan_model() is None:
            return "CHATGPT_PLAN_MODEL_MISSING"
        return None

    def status(self) -> ModelStatus:
        gemini_model = self._gemini_model()
        blocker: GeminiBlocker | None = None
        if not self._environment.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip():
            blocker = "GEMINI_API_KEY_MISSING"
        elif gemini_model is None:
            blocker = "GEMINI_MODEL_MISSING"
        chatgpt_plan_model = self._chatgpt_plan_model()
        chatgpt_plan_blocker = self._chatgpt_plan_blocker()
        if self._provider == "gemini":
            model = gemini_model or ""
        elif self._provider == "chatgpt_plan":
            model = chatgpt_plan_model or ""
        else:
            model = self._base.lm_studio_model
        return ModelStatus(
            provider=self._provider,
            model=model,
            cloud=self._provider in CLOUD_MODEL_PROVIDERS,
            local_model=self._base.lm_studio_model,
            gemini_model=gemini_model,
            gemini_ready=blocker is None,
            gemini_blocker=blocker,
            last_failure=self._last_failure,
            chatgpt_plan_model=chatgpt_plan_model,
            chatgpt_plan_ready=chatgpt_plan_blocker is None,
            chatgpt_plan_blocker=chatgpt_plan_blocker,
        )

    def effective_settings(self) -> AppSettings:
        """The settings every model-bound component should be built from right now."""
        if self._provider == "gemini":
            return replace(
                self._base,
                model_provider="gemini",
                gemini_model=self._gemini_model(),
                chatgpt_plan_model=None,
            )
        if self._provider == "chatgpt_plan":
            return replace(
                self._base,
                model_provider="chatgpt_plan",
                gemini_model=None,
                chatgpt_plan_model=self._chatgpt_plan_model(),
            )
        return replace(
            self._base, model_provider="lm_studio", gemini_model=None, chatgpt_plan_model=None
        )

    def record_failure(self, summary: str) -> None:
        """Remember the last model problem for the page; only safe characters are kept."""
        cleaned = re.sub(r"[^A-Za-z0-9_ .:=()/-]", "", summary).strip()[:200]
        self._last_failure = cleaned or None

    def record_success(self) -> None:
        """A model call worked: the earlier problem no longer describes the situation."""
        self._last_failure = None

    async def gemini_models(self) -> tuple[str, ...]:
        """The chat models the Gemini key can use, fetched on demand and kept for 5 minutes."""
        if not self._environment.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip():
            raise ModelSelectionError("GEMINI_NOT_READY")
        if self._models_cache is not None:
            fetched_at, names = self._models_cache
            if time.monotonic() - fetched_at < _MODEL_LIST_TTL_SECONDS:
                return names
        if self._gemini_lister is None:
            raise ModelSelectionError("MODEL_LIST_FAILED")
        try:
            names = tuple(await self._gemini_lister())
        except Exception:
            raise ModelSelectionError("MODEL_LIST_FAILED") from None
        self._models_cache = (time.monotonic(), names)
        return names

    async def chatgpt_plan_models(self) -> tuple[str, ...]:
        """The models the signed-in ChatGPT plan offers, kept for 5 minutes."""
        if self._chatgpt_plan_sign_in() != "SIGNED_IN":
            raise ModelSelectionError("CHATGPT_PLAN_NOT_READY")
        if self._chatgpt_plan_cache is not None:
            fetched_at, names = self._chatgpt_plan_cache
            if time.monotonic() - fetched_at < _MODEL_LIST_TTL_SECONDS:
                return names
        if self._chatgpt_plan_lister is None:
            raise ModelSelectionError("MODEL_LIST_FAILED")
        try:
            names = tuple(await self._chatgpt_plan_lister())
        except Exception:
            raise ModelSelectionError("MODEL_LIST_FAILED") from None
        self._chatgpt_plan_cache = (time.monotonic(), names)
        return names

    async def _check_chatgpt_plan_listed(self, model: str) -> None:
        """Refuse a model the plan does not offer (when the list can be read)."""
        try:
            names = await self.chatgpt_plan_models()
        except ModelSelectionError:
            return
        if names and model not in names:
            raise ModelSelectionError("INVALID_MODEL")

    async def _check_listed(self, model: str) -> None:
        """Refuse a model Google does not list for this key (when the list can be read)."""
        try:
            names = await self.gemini_models()
        except ModelSelectionError:
            return  # cannot verify; Google answers 404 and the panel shows the problem
        if names and model not in names:
            raise ModelSelectionError("INVALID_MODEL")

    # --- use tracking ------------------------------------------------------------------

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        """Mark one model-using request in flight; waits while a switch is in progress."""
        await self._gate.wait()
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1

    # --- switching ---------------------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        """Register something that must drop its model-bound state after a switch."""
        self._listeners.append(listener)

    async def switch(
        self,
        provider: str,
        *,
        confirm_cloud: bool = False,
        gemini_model: str | None = None,
        chatgpt_plan_model: str | None = None,
    ) -> ModelStatus:
        """Change the answering model: local, Gemini or the ChatGPT plan, and which model.

        Moving into a cloud provider needs confirmation, also from the other cloud
        provider; changing the model within the same provider does not (the data already
        goes there). A switch is refused while a question runs, and a failed rebuild puts
        everything back.
        """
        if provider not in MODEL_PROVIDERS:
            raise ModelSelectionError("INVALID_PROVIDER")
        wanted_model: str | None = None
        if gemini_model is not None:
            wanted_model = _normalize_model(gemini_model) if provider == "gemini" else None
            if wanted_model is None:
                raise ModelSelectionError("INVALID_MODEL")
        wanted_plan_model: str | None = None
        if chatgpt_plan_model is not None:
            wanted_plan_model = (
                _normalize_model(chatgpt_plan_model) if provider == "chatgpt_plan" else None
            )
            if wanted_plan_model is None:
                raise ModelSelectionError("INVALID_MODEL")
        async with self._select_lock:
            changing_model = (
                provider == "gemini"
                and wanted_model is not None
                and wanted_model != self._gemini_model()
            ) or (
                provider == "chatgpt_plan"
                and wanted_plan_model is not None
                and wanted_plan_model != self._chatgpt_plan_model()
            )
            if provider == self._provider and not changing_model:
                return self.status()
            if (
                provider in CLOUD_MODEL_PROVIDERS
                and provider != self._provider
                and not confirm_cloud
            ):
                raise ModelSelectionError("CLOUD_CONFIRMATION_REQUIRED")
            if provider == "gemini":
                if not self._environment.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip() or (
                    wanted_model is None and self._gemini_model() is None
                ):
                    raise ModelSelectionError("GEMINI_NOT_READY")
                if wanted_model is not None:
                    await self._check_listed(wanted_model)
            if provider == "chatgpt_plan":
                if self._chatgpt_plan_sign_in() != "SIGNED_IN" or (
                    wanted_plan_model is None and self._chatgpt_plan_model() is None
                ):
                    raise ModelSelectionError("CHATGPT_PLAN_NOT_READY")
                if wanted_plan_model is not None:
                    await self._check_chatgpt_plan_listed(wanted_plan_model)
            if self._active > 0:
                raise ModelSelectionError("MODEL_BUSY")

            previous = (self._provider, self._gemini_override, self._chatgpt_plan_override)
            self._gate.clear()
            try:
                self._provider = provider
                if wanted_model is not None:
                    self._gemini_override = wanted_model
                if wanted_plan_model is not None:
                    self._chatgpt_plan_override = wanted_plan_model
                for listener in self._listeners:
                    await listener()
            except Exception:
                (
                    self._provider,
                    self._gemini_override,
                    self._chatgpt_plan_override,
                ) = previous
                for listener in self._listeners:
                    try:
                        await listener()
                    except Exception:
                        continue
                raise ModelSelectionError("MODEL_SWITCH_FAILED") from None
            finally:
                self._gate.set()
            self._last_failure = None  # a new model starts with a clean slate
            return self.status()
