from __future__ import annotations

from scripts.phase9.release_check import (
    REQUIRED_REPORT_IDS,
    CheckStatus,
    ReleaseEnvironment,
    SanitizedEvidenceReport,
    run_release_checklist,
)
from scripts.phase9.release_manifest import (
    EXPECTED_MIGRATION_HEAD,
    EXPECTED_PHASE8_TARGET,
    Phase9ReleaseManifest,
)

RELEASE_COMMIT = "a" * 40


def _manifest() -> Phase9ReleaseManifest:
    return Phase9ReleaseManifest(
        schema_version="phase9-release-v1",
        release_commit=RELEASE_COMMIT,
        phase_tags=(("phase-08-passed", EXPECTED_PHASE8_TARGET),),
        migration_head=EXPECTED_MIGRATION_HEAD,
        artifact_hashes=(),
        repository_facts=(("tracked_clean", "YES"),),
        runtime_facts=(("python", "3.11"),),
        optional_local_model_state=(("local_model_state", "NOT_PROBED"),),
        external_write_accounting=(("real_external_writes", "0"),),
    )


def _report(
    report_id: str, *, status: CheckStatus = "PASS", writes: int = 0
) -> SanitizedEvidenceReport:
    return SanitizedEvidenceReport(
        report_id=report_id,
        status=status,
        summary=f"bounded evidence for {report_id}",
        command=f"verify {report_id}",
        evidence_sha256="b" * 64,
        external_writes=writes,
    )


def _environment(manifest: Phase9ReleaseManifest, *, clean: bool = True) -> ReleaseEnvironment:
    return ReleaseEnvironment(
        clean_tree=clean,
        release_commit=manifest.release_commit,
        phase_tags=manifest.phase_tags,
        migration_head=manifest.migration_head,
    )


def test_complete_release_checklist_is_ready_only_with_explicit_evidence() -> None:
    manifest = _manifest()
    result = run_release_checklist(
        manifest=manifest,
        reports=tuple(_report(report_id) for report_id in REQUIRED_REPORT_IDS),
        environment=_environment(manifest),
    )

    assert result.ready_for_human_acceptance is True
    assert result.critical_failures == 0
    assert result.important_findings == 0
    assert result.external_writes == 0
    assert result.secrets_exposed == 0
    assert {check.status for check in result.checks} == {"PASS"}


def test_missing_report_is_blocked_instead_of_assumed_pass() -> None:
    manifest = _manifest()
    reports = tuple(_report(report_id) for report_id in REQUIRED_REPORT_IDS[:-1])
    result = run_release_checklist(
        manifest=manifest,
        reports=reports,
        environment=_environment(manifest),
    )

    assert result.ready_for_human_acceptance is False
    assert result.checks[-1].status == "BLOCKED"


def test_manual_gate_remains_manual_and_nonzero_write_blocks() -> None:
    manifest = _manifest()
    reports = tuple(
        _report(
            report_id,
            status="MANUAL_GATE" if report_id == "docker_qualification" else "PASS",
            writes=1 if report_id == "action_lifecycle" else 0,
        )
        for report_id in REQUIRED_REPORT_IDS
    )
    result = run_release_checklist(
        manifest=manifest,
        reports=reports,
        environment=_environment(manifest),
    )

    assert result.ready_for_human_acceptance is False
    assert result.external_writes == 1
    assert (
        next(check for check in result.checks if check.check_id == "docker_qualification").status
        == "MANUAL_GATE"
    )


def test_dirty_tree_and_wrong_migration_are_blocked() -> None:
    manifest = _manifest()
    environment = ReleaseEnvironment(
        clean_tree=False,
        release_commit=manifest.release_commit,
        phase_tags=manifest.phase_tags,
        migration_head="0006_phase7e_task_records",
    )
    result = run_release_checklist(
        manifest=manifest,
        reports=tuple(_report(report_id) for report_id in REQUIRED_REPORT_IDS),
        environment=environment,
    )

    assert result.ready_for_human_acceptance is False
    assert (
        next(check for check in result.checks if check.check_id == "git_tree").status == "BLOCKED"
    )
    assert (
        next(check for check in result.checks if check.check_id == "migration_head").status
        == "BLOCKED"
    )
