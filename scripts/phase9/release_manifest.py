"""Canonical, secret-free Phase 9 release baseline manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = "phase9-release-v1"
EXPECTED_PHASE8_TARGET = "5dea7f19bbe26e85b968afc63a21c10f0dd4e389"
EXPECTED_MIGRATION_HEAD = "0007_phase8_visual_provenance"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_NAMES = re.compile(
    r"(?:secret|token|password|cookie|oauth|authorization|api[_-]?key|credential|private[_-]?key)",
    re.IGNORECASE,
)
_SAFE_FACT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _pairs(value: Iterable[tuple[str, str]], field_name: str) -> tuple[tuple[str, str], ...]:
    pairs = tuple(sorted(value))
    for name, fact in pairs:
        if not isinstance(name, str) or _SAFE_FACT_NAME.fullmatch(name) is None:
            raise ValueError(f"{field_name} contains an invalid fact name")
        if _FORBIDDEN_NAMES.search(name):
            raise ValueError(f"forbidden manifest field: {name}")
        if not isinstance(fact, str) or len(fact.encode("utf-8")) > 512:
            raise ValueError(f"{field_name} contains an invalid fact")
    if len({name for name, _ in pairs}) != len(pairs):
        raise ValueError(f"{field_name} contains duplicate names")
    return pairs


@dataclass(frozen=True, slots=True)
class Phase9ReleaseManifest:
    """Immutable release facts with no secret-bearing free-form metadata."""

    schema_version: str
    release_commit: str
    phase_tags: tuple[tuple[str, str], ...]
    migration_head: str
    artifact_hashes: tuple[tuple[str, str], ...]
    repository_facts: tuple[tuple[str, str], ...] = ()
    runtime_facts: tuple[tuple[str, str], ...] = ()
    optional_local_model_state: tuple[tuple[str, str], ...] = ()
    external_write_accounting: tuple[tuple[str, str], ...] = ()
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema")
        if _COMMIT.fullmatch(self.release_commit) is None:
            raise ValueError("release_commit must be a full commit SHA")
        if not self.phase_tags:
            raise ValueError("phase-08-passed is required")
        tags = _pairs(self.phase_tags, "phase_tags")
        if dict(tags).get("phase-08-passed") != EXPECTED_PHASE8_TARGET:
            raise ValueError("phase-08-passed target is not frozen")
        if self.migration_head != EXPECTED_MIGRATION_HEAD:
            raise ValueError("unexpected Alembic migration head")
        artifacts = _pairs(self.artifact_hashes, "artifact_hashes")
        for _, digest in artifacts:
            if _SHA256.fullmatch(digest) is None:
                raise ValueError("artifact hash must be lowercase SHA-256")
        repository = _pairs(self.repository_facts, "repository_facts")
        runtime = _pairs(self.runtime_facts, "runtime_facts")
        models = _pairs(self.optional_local_model_state, "optional_local_model_state")
        writes = _pairs(self.external_write_accounting, "external_write_accounting")
        object.__setattr__(self, "phase_tags", tags)
        object.__setattr__(self, "artifact_hashes", artifacts)
        object.__setattr__(self, "repository_facts", repository)
        object.__setattr__(self, "runtime_facts", runtime)
        object.__setattr__(self, "optional_local_model_state", models)
        object.__setattr__(self, "external_write_accounting", writes)
        object.__setattr__(self, "sha256", _manifest_hash(self))

    def as_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "artifact_hashes": dict(self.artifact_hashes),
            "external_write_accounting": dict(self.external_write_accounting),
            "migration_head": self.migration_head,
            "optional_local_model_state": dict(self.optional_local_model_state),
            "phase_tags": dict(self.phase_tags),
            "release_commit": self.release_commit,
            "repository_facts": dict(self.repository_facts),
            "runtime_facts": dict(self.runtime_facts),
            "schema_version": self.schema_version,
        }
        if include_hash:
            result["sha256"] = self.sha256
        return result


def canonical_manifest_bytes(manifest: Phase9ReleaseManifest) -> bytes:
    """Serialize a manifest without its derived hash using canonical JSON."""

    if not isinstance(manifest, Phase9ReleaseManifest):
        raise TypeError("manifest must be a Phase9ReleaseManifest")
    return (
        json.dumps(
            manifest.as_dict(include_hash=False),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _manifest_hash(manifest: Phase9ReleaseManifest) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def manifest_sha256(manifest: Phase9ReleaseManifest) -> str:
    """Return the canonical SHA-256 for the immutable manifest contents."""

    return _manifest_hash(manifest)


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo_root), *args),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if completed.returncode != 0:
        raise ValueError(f"git metadata unavailable for {args[0]}")
    return completed.stdout.strip()


def _command_version(command: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNAVAILABLE"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0][:256] if completed.returncode == 0 and output else "UNAVAILABLE"


def collect_runtime_facts() -> tuple[tuple[str, str], ...]:
    """Collect non-secret machine/tool facts; no service is contacted."""

    return tuple(
        sorted(
            (
                ("docker", _command_version(("docker", "--version"))),
                ("node", _command_version(("node", "--version"))),
                ("npm", _command_version(("npm", "--version"))),
                ("python", sys.version.split()[0]),
                ("uv", _command_version(("uv", "--version"))),
                ("os", os.name),
            )
        )
    )


def _migration_head(repo_root: Path) -> str:
    versions = sorted((repo_root / "migrations" / "versions").glob("*.py"))
    if not versions:
        raise ValueError("migration versions are absent")
    matches = [
        path.stem for path in versions if path.stem.startswith("0007_phase8_visual_provenance")
    ]
    if matches != [EXPECTED_MIGRATION_HEAD]:
        raise ValueError("expected Phase 8 migration head is absent")
    return EXPECTED_MIGRATION_HEAD


def verify_phase8_freeze(*, repo_root: Path, expected_target: str) -> None:
    """Verify the immutable Phase 8 tag and current migration head."""

    if expected_target != EXPECTED_PHASE8_TARGET:
        raise ValueError("unexpected Phase 8 target")
    target = _git(repo_root, "rev-parse", "phase-08-passed^{}")
    if target != expected_target:
        raise ValueError("phase-08-passed target does not match frozen baseline")
    if _migration_head(repo_root) != EXPECTED_MIGRATION_HEAD:
        raise ValueError("migration head does not match frozen baseline")


def _hash_artifact(repo_root: Path, path: Path) -> tuple[str, str]:
    resolved_root = repo_root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError("artifact must be inside repository")
    if not resolved_path.is_file():
        raise ValueError("artifact must be a file")
    return str(resolved_path.relative_to(resolved_root)).replace("\\", "/"), hashlib.sha256(
        resolved_path.read_bytes()
    ).hexdigest()


def build_release_manifest(
    *,
    repo_root: Path,
    release_commit: str,
    phase_tags: tuple[tuple[str, str], ...] | None = None,
    migration_head: str | None = None,
    artifact_paths: tuple[Path, ...] = (),
    repository_facts: tuple[tuple[str, str], ...] = (),
    runtime_facts: tuple[tuple[str, str], ...] | None = None,
    optional_local_model_state: tuple[tuple[str, str], ...] = (),
    external_write_accounting: tuple[tuple[str, str], ...] = (("real_external_writes", "0"),),
) -> Phase9ReleaseManifest:
    """Build a baseline from explicit repository facts and bounded artifacts."""

    root = repo_root.resolve()
    resolved_tags = phase_tags or (
        ("phase-08-passed", _git(root, "rev-parse", "phase-08-passed^{}")),
    )
    resolved_migration = migration_head or _migration_head(root)
    artifacts = tuple(_hash_artifact(root, path) for path in artifact_paths)
    facts = repository_facts or (("tracked_clean", "NOT_CHECKED"),)
    models = optional_local_model_state or (("local_model_state", "NOT_PROBED"),)
    return Phase9ReleaseManifest(
        schema_version=SCHEMA_VERSION,
        release_commit=release_commit,
        phase_tags=resolved_tags,
        migration_head=resolved_migration,
        artifact_hashes=artifacts,
        repository_facts=facts,
        runtime_facts=runtime_facts if runtime_facts is not None else collect_runtime_facts(),
        optional_local_model_state=models,
        external_write_accounting=external_write_accounting,
    )


__all__ = [
    "EXPECTED_MIGRATION_HEAD",
    "EXPECTED_PHASE8_TARGET",
    "Phase9ReleaseManifest",
    "build_release_manifest",
    "canonical_manifest_bytes",
    "collect_runtime_facts",
    "manifest_sha256",
    "verify_phase8_freeze",
]
