from __future__ import annotations

import pytest

from tests.phase9.evaluation.harness import EvaluationAdapters, run_deterministic_evaluation
from tests.phase9.evaluation.manifest import build_manifest


def test_default_evaluation_is_domain_separated_and_side_effect_free() -> None:
    report = run_deterministic_evaluation(build_manifest())
    assert report.manifest_sha256 == build_manifest().sha256
    assert {item.name for item in report.domain_reports} == {
        "retrieval",
        "grounding_nli",
        "citation",
        "agent_tool",
        "multimodal",
        "computation",
    }
    assert report.model_calls == 0
    assert report.rag_calls == 0
    assert report.external_writes == 0
    assert report.fixture_mutations == 0
    assert report.blended_primary_score is None


def test_normal_harness_cannot_execute_real_heldout_ids() -> None:
    with pytest.raises(ValueError, match="real heldout"):
        unsafe = build_manifest()
        object.__setattr__(unsafe, "calibration_ids", ("hold-001",))
        run_deterministic_evaluation(unsafe)


def test_custom_adapters_are_bounded_and_cannot_add_authority() -> None:
    class FakeAdapters(EvaluationAdapters):
        pass

    report = run_deterministic_evaluation(build_manifest(), adapters=FakeAdapters())
    assert report.external_writes == 0
