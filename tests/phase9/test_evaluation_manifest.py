from __future__ import annotations

import pytest

from tests.phase9.evaluation.manifest import (
    CALIBRATION_IDS,
    DOMAINS,
    HELDOUT_IDS,
    Phase9EvaluationManifest,
    build_manifest,
)


def test_manifest_is_frozen_disjoint_and_balanced() -> None:
    manifest = build_manifest()
    assert isinstance(manifest, Phase9EvaluationManifest)
    assert manifest.calibration_ids == CALIBRATION_IDS
    assert manifest.heldout_ids == HELDOUT_IDS
    assert not set(manifest.calibration_ids) & set(manifest.heldout_ids)
    assert manifest.domains == DOMAINS
    assert manifest.language_counts == {"en": 9, "ar": 9}
    assert manifest.heldout_language_counts == {"en": 4, "ar": 4}
    assert len(manifest.sha256) == 64


def test_manifest_rejects_duplicates_overlap_unknown_domains_and_real_heldout() -> None:
    with pytest.raises(ValueError):
        Phase9EvaluationManifest.from_ids(("cal-en-001", "cal-en-001"), ("holdout-ar-001",))
    with pytest.raises(ValueError):
        Phase9EvaluationManifest.from_ids(("cal-en-001",), ("cal-en-001",))
    with pytest.raises(ValueError):
        Phase9EvaluationManifest.from_ids(
            ("cal-en-001",), ("holdout-ar-001",), domains=("untrusted",)
        )
    with pytest.raises(ValueError):
        Phase9EvaluationManifest.from_ids(("hold-001",), ("holdout-ar-001",))
