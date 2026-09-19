from __future__ import annotations

from aaos.phase8.evaluation import (
    PHASE8_CALIBRATION_IDS,
    PHASE8_HELDOUT_IDS,
    Phase8EvaluationManifest,
)

CALIBRATION_IDS = PHASE8_CALIBRATION_IDS
HELDOUT_IDS = PHASE8_HELDOUT_IDS

FROZEN_PHASE8_MANIFEST = Phase8EvaluationManifest.from_ids(CALIBRATION_IDS, HELDOUT_IDS)

__all__ = [
    "CALIBRATION_IDS",
    "FROZEN_PHASE8_MANIFEST",
    "HELDOUT_IDS",
    "Phase8EvaluationManifest",
]
