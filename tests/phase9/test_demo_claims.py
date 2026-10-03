from __future__ import annotations

from pathlib import Path

import pytest

from scripts.phase9.public_release import PortfolioClaim, build_demo_steps


def test_demo_steps_label_real_fake_and_synthetic_behavior() -> None:
    steps = build_demo_steps()
    assert steps
    assert {step.label for step in steps} == {
        "REAL_LOCAL_BEHAVIOR",
        "DETERMINISTIC_FAKE_BEHAVIOR",
        "SYNTHETIC_DATA",
    }
    assert all(step.external_writes == 0 for step in steps)
    assert any("RAG" in step.label or "RAG" in step.action for step in steps)


def test_claims_require_bounded_evidence_and_closed_scope() -> None:
    claim = PortfolioClaim(
        claim_id="grounded-rag",
        text="Grounded local RAG preserves citation lineage.",
        evidence_refs=("phase9-evaluation",),
        scope="REAL_LOCAL",
    )
    assert claim.scope == "REAL_LOCAL"
    with pytest.raises(ValueError):
        PortfolioClaim("unsupported", "No evidence", (), "REAL_LOCAL")
    with pytest.raises(ValueError):
        PortfolioClaim("bad-scope", "Bounded", ("evidence",), "LIVE_PROVIDER")  # type: ignore[arg-type]


def test_repository_status_truthfully_records_phase8_and_phase9_frozen() -> None:
    # Phase 9 was human-accepted and tagged phase-09-passed (GOVERNANCE_LEDGER.md).
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert "Phase 8: **FROZEN**" in readme
    assert "Phase 9: **FROZEN**" in readme
    assert "Phase 9: **IN PROGRESS**" not in readme
