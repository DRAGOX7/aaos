from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from scripts.phase9.acceptance import (
    Phase9AcceptanceInputs,
    run_phase9_acceptance,
)
from scripts.phase9.release_check import EvidenceIndex
from scripts.phase9.release_manifest import EXPECTED_MIGRATION_HEAD, EXPECTED_PHASE8_TARGET

STARTING_HEAD = "a" * 40
FINAL_HEAD = "b" * 40
MANIFEST_SHA = "c" * 64
INDEX_SHA = "d" * 64
REPORT_SHA = "e" * 64


def _inputs(**changes: object) -> Phase9AcceptanceInputs:
    base = Phase9AcceptanceInputs(
        starting_head=STARTING_HEAD,
        final_head=FINAL_HEAD,
        expected_release_head=STARTING_HEAD,
        phase8_tag_target=EXPECTED_PHASE8_TARGET,
        migration_head=EXPECTED_MIGRATION_HEAD,
        manifest_sha256=MANIFEST_SHA,
        evidence_index=EvidenceIndex(
            MANIFEST_SHA, (("backend_tests", REPORT_SHA),), ("backend_tests.json",)
        ),
        test_totals=(
            ("backend_passed", "3193"),
            ("backend_skipped", "6"),
            ("frontend_passed", "173"),
        ),
        clean_tree=True,
        manual_gates_complete=True,
        cleanup_ok=True,
        external_writes=0,
        model_downloads=0,
        provider_calls=0,
        rlm_work=0,
        critical_findings=0,
        important_findings=0,
        unresolved_secret_findings=0,
        human_decision="PENDING",
    )
    replace_func = cast(Any, replace)
    return cast(Phase9AcceptanceInputs, replace_func(base, **changes))


def test_clean_evidence_is_ready_but_not_human_accepted() -> None:
    report = run_phase9_acceptance(_inputs())

    assert report.ready_for_human_freeze_acceptance is True
    assert report.human_accepted is False
    assert report.blockers == ()


@pytest.mark.parametrize(
    "changes, expected",
    (
        ({"clean_tree": False}, "dirty tree"),
        ({"human_decision": None}, "human decision"),
        ({"external_writes": 1}, "external writes"),
        ({"unresolved_secret_findings": 1}, "secret/privacy"),
        ({"manual_gates_complete": False}, "manual gate"),
        ({"phase8_tag_target": "f" * 40}, "Phase 8 tag"),
    ),
)
def test_acceptance_blocks_unsafe_or_unapproved_state(
    changes: dict[str, object], expected: str
) -> None:
    report = run_phase9_acceptance(_inputs(**changes))

    assert report.ready_for_human_freeze_acceptance is False
    assert any(expected in blocker for blocker in report.blockers)


def test_missing_manifest_or_report_hash_blocks_acceptance() -> None:
    with pytest.raises(ValueError, match="manifest_sha256"):
        _inputs(manifest_sha256="")
    missing_report = run_phase9_acceptance(
        _inputs(evidence_index=EvidenceIndex(MANIFEST_SHA, (), ()))
    )

    assert missing_report.ready_for_human_freeze_acceptance is False


def test_acceptance_rejects_mismatched_manifest_and_index() -> None:
    report = run_phase9_acceptance(
        _inputs(
            evidence_index=EvidenceIndex(
                "f" * 64, (("backend_tests", REPORT_SHA),), ("backend_tests.json",)
            )
        )
    )

    assert report.ready_for_human_freeze_acceptance is False
    assert any("manifest" in blocker for blocker in report.blockers)
