from __future__ import annotations

import importlib

import pytest


def test_vision_lifecycle_is_sequential_and_explicit() -> None:
    from aaos.inference.vision_models import VisionLifecycleError, VisionModelLifecycle

    lifecycle = VisionModelLifecycle()
    lifecycle.ensure_loaded("local-vision", "rev-1")
    with pytest.raises(VisionLifecycleError, match="vision"):
        lifecycle.ensure_loaded("other-vision", "rev-2")
    lifecycle.release()
    lifecycle.assert_sequential_with_bge()


def test_bge_and_vision_residency_cannot_overlap() -> None:
    from aaos.inference.vision_models import VisionLifecycleError, VisionModelLifecycle

    lifecycle = VisionModelLifecycle()
    lifecycle.mark_bge_loaded()
    with pytest.raises(VisionLifecycleError, match="bge"):
        lifecycle.ensure_loaded("local-vision", "rev-1")
    lifecycle.mark_bge_released()
    lifecycle.ensure_loaded("local-vision", "rev-1")
    lifecycle.release()


def test_importing_vision_modules_does_not_load_or_download_models() -> None:
    module = importlib.import_module("aaos.inference.vision_models")
    assert module.VisionModelLifecycle().loaded_model is None
