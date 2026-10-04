"""Which model answers right now, and the one safe way to change it at runtime.

The choice is process memory only: a restart returns to whatever the environment
selected (the local model unless the owner opted in), so the safe default always wins.
The Gemini API key is never part of this state; readiness only records whether it is
present in the environment, and the gateway reads it itself when it is built.

Switching refuses to run while a question is in flight, sends nothing to Google
without an explicit confirmation, and asks every holder of a model-bound component
(the assistant, the RAG runtime) to drop it so it is rebuilt for the new model.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Literal

from aaos.config import (
    GEMINI_API_KEY_ENVIRONMENT_KEY,
    GEMINI_MODEL_ENVIRONMENT_KEY,
    MODEL_PROVIDERS,
    AppSettings,
)

GeminiBlocker = Literal["GEMINI_API_KEY_MISSING", "GEMINI_MODEL_MISSING"]
SelectionErrorCode = Literal[
    "INVALID_PROVIDER",
    "CLOUD_CONFIRMATION_REQUIRED",
    "GEMINI_NOT_READY",
    "MODEL_BUSY",
    "MODEL_SWITCH_FAILED",
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


Listener = Callable[[], Awaitable[None]]


class ModelSelection:
    """Shared, in-memory model choice for one running AAOS process."""

    def __init__(self, settings: AppSettings, environment: Mapping[str, str] | None = None) -> None:
        self._base = settings
        self._environment = environment if environment is not None else os.environ
        self._provider = settings.model_provider
        self._listeners: list[Listener] = []
        self._active = 0
        self._select_lock = asyncio.Lock()
        self._gate = asyncio.Event()
        self._gate.set()

    # --- reading ------------------------------------------------------------------------

    def _gemini_model(self) -> str | None:
        configured = self._base.gemini_model or self._environment.get(GEMINI_MODEL_ENVIRONMENT_KEY)
        if configured is None or not configured.strip():
            return None
        return configured.strip().removeprefix("models/")

    def status(self) -> ModelStatus:
        gemini_model = self._gemini_model()
        blocker: GeminiBlocker | None = None
        if not self._environment.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip():
            blocker = "GEMINI_API_KEY_MISSING"
        elif gemini_model is None:
            blocker = "GEMINI_MODEL_MISSING"
        cloud = self._provider == "gemini"
        return ModelStatus(
            provider=self._provider,
            model=(gemini_model or "") if cloud else self._base.lm_studio_model,
            cloud=cloud,
            local_model=self._base.lm_studio_model,
            gemini_model=gemini_model,
            gemini_ready=blocker is None,
            gemini_blocker=blocker,
        )

    def effective_settings(self) -> AppSettings:
        """The settings every model-bound component should be built from right now."""
        if self._provider == "gemini":
            return replace(self._base, model_provider="gemini", gemini_model=self._gemini_model())
        return replace(self._base, model_provider="lm_studio", gemini_model=None)

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

    async def switch(self, provider: str, *, confirm_cloud: bool = False) -> ModelStatus:
        if provider not in MODEL_PROVIDERS:
            raise ModelSelectionError("INVALID_PROVIDER")
        async with self._select_lock:
            if provider == self._provider:
                return self.status()
            if provider == "gemini":
                if not confirm_cloud:
                    raise ModelSelectionError("CLOUD_CONFIRMATION_REQUIRED")
                if not self.status().gemini_ready:
                    raise ModelSelectionError("GEMINI_NOT_READY")
            if self._active > 0:
                raise ModelSelectionError("MODEL_BUSY")

            previous = self._provider
            self._gate.clear()
            try:
                self._provider = provider
                for listener in self._listeners:
                    await listener()
            except Exception:
                self._provider = previous
                for listener in self._listeners:
                    try:
                        await listener()
                    except Exception:
                        continue
                raise ModelSelectionError("MODEL_SWITCH_FAILED") from None
            finally:
                self._gate.set()
            return self.status()
