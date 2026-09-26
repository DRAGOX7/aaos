from __future__ import annotations

import pytest

from aaos.phase8.acceptance import Phase8AcceptanceReport, run_phase8_acceptance


def test_phase8_acceptance_is_bounded_synthetic_and_zero_write() -> None:
    report = run_phase8_acceptance()

    assert isinstance(report, Phase8AcceptanceReport)
    assert report.computation_case_count > 0
    assert report.vision_case_count > 0
    assert report.composition_case_count > 0
    assert report.model_call_count == 0
    assert report.rag_execution_count == 0
    assert report.external_write_count == 0
    assert report.retry_count == 0
    assert report.heldout_execution_count == 0
    assert report.phase6_bypass_count == 0
    assert report.browser_execution_route_count == 0
    assert report.rlm_work is False


def test_phase8_acceptance_report_has_closed_serialization() -> None:
    report = run_phase8_acceptance()
    payload = report.to_dict()

    assert set(payload) == {
        "schema_version",
        "manifest_sha256",
        "computation_case_count",
        "computation_pass_count",
        "vision_case_count",
        "vision_pass_count",
        "composition_case_count",
        "composition_pass_count",
        "security_negative_pass_count",
        "model_call_count",
        "rag_execution_count",
        "external_write_count",
        "retry_count",
        "heldout_execution_count",
        "phase6_bypass_count",
        "browser_execution_route_count",
        "rlm_work",
    }
    with pytest.raises(TypeError):
        run_phase8_acceptance(details={"code": "not allowed"})


def test_phase8_acceptance_keeps_unknown_outcome_and_visual_fallback_covered() -> None:
    report = run_phase8_acceptance()

    assert report.security_negative_pass_count >= 1
    assert report.computation_pass_count <= report.computation_case_count
    assert report.vision_pass_count <= report.vision_case_count


def test_phase8_acceptance_has_no_real_heldout_or_provider_path() -> None:
    report = run_phase8_acceptance()

    assert report.heldout_execution_count == 0
    assert report.model_call_count == 0
    assert report.rag_execution_count == 0
    assert report.external_write_count == 0
