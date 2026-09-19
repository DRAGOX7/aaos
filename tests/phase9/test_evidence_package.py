from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from scripts.phase9.release_check import (
    SanitizedEvidenceReport,
    build_evidence_package,
)
from scripts.phase9.release_manifest import (
    EXPECTED_MIGRATION_HEAD,
    EXPECTED_PHASE8_TARGET,
    Phase9ReleaseManifest,
)


def _manifest() -> Phase9ReleaseManifest:
    return Phase9ReleaseManifest(
        schema_version="phase9-release-v1",
        release_commit="a" * 40,
        phase_tags=(("phase-08-passed", EXPECTED_PHASE8_TARGET),),
        migration_head=EXPECTED_MIGRATION_HEAD,
        artifact_hashes=(),
        repository_facts=(("tracked_clean", "YES"),),
        runtime_facts=(("python", "3.11"),),
        optional_local_model_state=(("local_model_state", "NOT_PROBED"),),
        external_write_accounting=(("real_external_writes", "0"),),
    )


def _report(report_id: str) -> SanitizedEvidenceReport:
    return SanitizedEvidenceReport(
        report_id=report_id,
        status="PASS",
        summary="sanitized bounded evidence",
        command="pytest -q",
        evidence_sha256="c" * 64,
    )


def test_evidence_package_writes_canonical_hashed_sanitized_files(tmp_path: Path) -> None:
    index = build_evidence_package(
        manifest=_manifest(),
        reports=(_report("backend_tests"), _report("public_release")),
        output_dir=tmp_path / "evidence",
    )

    assert index.manifest_sha256 == _manifest().sha256
    assert index.index_sha256 and len(index.index_sha256) == 64
    assert (tmp_path / "evidence" / "backend_tests.json").is_file()
    assert (tmp_path / "evidence" / "public_release.json").is_file()
    index_payload = json.loads((tmp_path / "evidence" / "index.json").read_text())
    assert index_payload["manifest_sha256"] == _manifest().sha256
    assert (
        "sanitized bounded evidence" in (tmp_path / "evidence" / "backend_tests.json").read_text()
    )
    assert "raw_payload" not in index_payload


def test_evidence_package_rejects_raw_objects_secret_text_and_reuse(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        build_evidence_package(
            manifest=_manifest(),
            reports=cast(tuple[SanitizedEvidenceReport, ...], ({"raw": "payload"},)),
            output_dir=tmp_path / "one",
        )
    with pytest.raises(ValueError, match="sensitive"):
        build_evidence_package(
            manifest=_manifest(),
            reports=(
                SanitizedEvidenceReport(
                    report_id="backend_tests",
                    status="PASS",
                    summary="access_token: leaked",
                    command="pytest -q",
                    evidence_sha256="c" * 64,
                ),
            ),
            output_dir=tmp_path / "two",
        )
    output = tmp_path / "three"
    output.mkdir()
    (output / "existing.json").write_text("{}")
    with pytest.raises(FileExistsError):
        build_evidence_package(
            manifest=_manifest(), reports=(_report("backend_tests"),), output_dir=output
        )
