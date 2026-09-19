"""Explicit sequential lifecycle for an optional local vision model."""

from __future__ import annotations

from aaos.inference.models import VisionModelReference


class VisionLifecycleError(RuntimeError):
    """Fixed lifecycle error without provider or host details."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " ").lower())


class VisionModelLifecycle:
    def __init__(self) -> None:
        self._loaded_model: VisionModelReference | None = None
        self._bge_loaded = False

    @property
    def loaded_model(self) -> VisionModelReference | None:
        return self._loaded_model

    def ensure_loaded(self, model_id: str, revision: str) -> None:
        if self._bge_loaded:
            raise VisionLifecycleError("BGE_ACTIVE")
        requested = VisionModelReference(model_id=model_id, revision=revision)
        if self._loaded_model is not None and self._loaded_model != requested:
            raise VisionLifecycleError("VISION_MODEL_ALREADY_LOADED")
        self._loaded_model = requested

    def release(self) -> None:
        self._loaded_model = None

    def mark_bge_loaded(self) -> None:
        if self._loaded_model is not None:
            raise VisionLifecycleError("VISION_ACTIVE")
        self._bge_loaded = True

    def mark_bge_released(self) -> None:
        self._bge_loaded = False

    def assert_sequential_with_bge(self) -> None:
        if self._loaded_model is not None and self._bge_loaded:
            raise VisionLifecycleError("CONCURRENT_GPU_RESIDENCY")


__all__ = ["VisionLifecycleError", "VisionModelLifecycle"]
