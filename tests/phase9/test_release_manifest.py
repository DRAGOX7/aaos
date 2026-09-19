from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.phase9.release_manifest import (
    EXPECTED_PHASE8_TARGET,
    Phase9ReleaseManifest,
    build_release_manifest,
    canonical_manifest_bytes,
    manifest_sha256,
)


def test_manifest_serialization_and_hash_are_deterministic(tmp_path: Path) -> None:
    artifact = tmp_path / "report.json"
    artifact.write_bytes(b'{"status":"PASS"}\n')
    manifest = build_release_manifest(
        repo_root=tmp_path,
        release_commit="a" * 40,
        phase_tags=(("phase-08-passed", EXPECTED_PHASE8_TARGET),),
        migration_head="0007_phase8_visual_provenance",
        artifact_paths=(artifact,),
        repository_facts=(("tracked_clean", "YES"),),
        runtime_facts=(("python", "3.11"),),
        optional_local_model_state=(("qwen", "NOT_PROBED"),),
        external_write_accounting=(("real_external_writes", "0"),),
    )
    first = canonical_manifest_bytes(manifest)
    second = canonical_manifest_bytes(manifest)
    assert first == second
    assert manifest_sha256(manifest) == hashlib.sha256(first).hexdigest()
    assert manifest.sha256 == manifest_sha256(manifest)


def test_manifest_is_strict_and_rejects_secret_bearing_fact_names() -> None:
    with pytest.raises(ValueError, match="forbidden manifest field"):
        Phase9ReleaseManifest(
            schema_version="phase9-release-v1",
            release_commit="a" * 40,
            phase_tags=(("phase-08-passed", EXPECTED_PHASE8_TARGET),),
            migration_head="0007_phase8_visual_provenance",
            artifact_hashes=(),
            repository_facts=(("api_key", "redacted"),),
            runtime_facts=(),
            optional_local_model_state=(),
            external_write_accounting=(("real_external_writes", "0"),),
        )


def test_artifacts_must_be_contained_by_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-report.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact must be inside repository"):
        build_release_manifest(
            repo_root=tmp_path,
            release_commit="a" * 40,
            phase_tags=(("phase-08-passed", EXPECTED_PHASE8_TARGET),),
            migration_head="0007_phase8_visual_provenance",
            artifact_paths=(outside,),
        )


def test_phase8_freeze_and_migration_are_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="phase-08-passed"):
        build_release_manifest(
            repo_root=tmp_path,
            release_commit="a" * 40,
            phase_tags=(("not-a-tag", "a" * 40),),
            migration_head="0007_phase8_visual_provenance",
            artifact_paths=(),
        )
