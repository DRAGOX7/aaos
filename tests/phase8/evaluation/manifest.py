from __future__ import annotations

from aaos.phase8.evaluation import Phase8EvaluationManifest

CALIBRATION_IDS = (
    "cal-compute-success",
    "cal-compute-policy-denied",
    "cal-compute-timeout",
    "cal-compute-unknown",
    "cal-vision-text",
    "cal-vision-diagram",
    "cal-vision-chart",
    "cal-vision-table",
    "cal-vision-equation",
    "cal-vision-partial",
    "cal-vision-unavailable",
    "cal-vision-malformed",
    "cal-composition-injection",
)

HELDOUT_IDS = (
    "synthetic-holdout-selection",
    "synthetic-holdout-provenance",
    "synthetic-holdout-fallback",
)

FROZEN_PHASE8_MANIFEST = Phase8EvaluationManifest.from_ids(CALIBRATION_IDS, HELDOUT_IDS)

__all__ = [
    "CALIBRATION_IDS",
    "FROZEN_PHASE8_MANIFEST",
    "HELDOUT_IDS",
    "Phase8EvaluationManifest",
]
