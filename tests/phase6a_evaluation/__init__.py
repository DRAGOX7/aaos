"""Deterministic, trusted-core Phase 6A evaluation fixtures."""

from .manifest import (
    CALIBRATION_CASE_COUNT,
    HELDOUT_CASE_COUNT,
    EvaluationCategory,
    EvaluationInvariant,
    EvaluationSetup,
    FrozenEvaluationManifest,
    Phase6AEvaluationCase,
    build_calibration_cases,
    build_heldout_cases,
    category_counts,
    freeze_manifest,
    language_fixture_ids,
)

__all__ = [
    "CALIBRATION_CASE_COUNT",
    "HELDOUT_CASE_COUNT",
    "EvaluationCategory",
    "EvaluationInvariant",
    "EvaluationSetup",
    "FrozenEvaluationManifest",
    "Phase6AEvaluationCase",
    "build_calibration_cases",
    "build_heldout_cases",
    "category_counts",
    "freeze_manifest",
    "language_fixture_ids",
]
