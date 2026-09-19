from __future__ import annotations

import pytest

from aaos.phase8.evaluation import Phase8Report, run_phase8_calibration
from tests.phase8.evaluation.manifest import (
    FROZEN_PHASE8_MANIFEST,
    Phase8EvaluationManifest,
)


def test_frozen_manifest_is_deterministic_and_disjoint() -> None:
    manifest = FROZEN_PHASE8_MANIFEST

    assert manifest.sha256
    assert not set(manifest.calibration_ids) & set(manifest.heldout_ids)
    assert all(case_id.startswith("cal-") for case_id in manifest.calibration_ids)
    assert all(case_id.startswith("synthetic-holdout-") for case_id in manifest.heldout_ids)
    assert manifest == Phase8EvaluationManifest.from_ids(
        manifest.calibration_ids, manifest.heldout_ids
    )


def test_manifest_rejects_duplicate_or_overlapping_membership() -> None:
    with pytest.raises(ValueError):
        Phase8EvaluationManifest.from_ids(("cal-001", "cal-001"), ("synthetic-holdout-001",))
    with pytest.raises(ValueError):
        Phase8EvaluationManifest.from_ids(("cal-001",), ("cal-001",))


def test_calibration_report_is_trusted_core_only() -> None:
    report = run_phase8_calibration(FROZEN_PHASE8_MANIFEST)

    assert isinstance(report, Phase8Report)
    assert report.manifest_sha256 == FROZEN_PHASE8_MANIFEST.sha256
    assert report.model_call_count == 0
    assert report.rag_execution_count == 0
    assert report.external_write_count == 0
    assert report.retry_count == 0
    assert report.heldout_execution_count == 0
    assert report.computation_case_count > 0
    assert report.vision_case_count > 0


def test_calibration_does_not_accept_real_heldout_ids() -> None:
    manifest = Phase8EvaluationManifest.from_ids(
        ("hold-001",), ("synthetic-holdout-001",)
    )

    with pytest.raises(ValueError):
        run_phase8_calibration(manifest)

