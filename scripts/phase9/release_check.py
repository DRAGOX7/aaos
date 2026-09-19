"""Deterministic Phase 9 release checklist and sanitized evidence package."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from scripts.phase9.release_manifest import (
    EXPECTED_MIGRATION_HEAD,
    Phase9ReleaseManifest,
)

CheckStatus = Literal["PASS", "NOT_APPLICABLE", "MANUAL_GATE", "BLOCKED"]

REQUIRED_REPORT_IDS: tuple[str, ...] = (
    "backend_tests",
    "frontend_tests",
    "static_gates",
    "api_drift",
    "migration_verification",
    "security_adversarial",
    "docker_qualification",
    "browser_acceptance",
    "evaluation",
    "multimodal_evaluation",
    "action_lifecycle",
    "failure_recovery",
    "clean_setup",
    "windows_qualification",
    "performance_resources",
    "public_release",
    "documentation_claims",
    "cleanup",
    "independent_review",
)

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SENSITIVE = re.compile(
    r"(?:access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|"
    r"authorization\s*[:=]|cookie\s*[:=]|password\s*[:=]|api[_ -]?key\s*[:=])",
    re.IGNORECASE,
)


def _text(value: str, field_name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{field_name} is not bounded")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} must be single-line")
    if _SENSITIVE.search(value):
        raise ValueError(f"sensitive text is not allowed in {field_name}")
    return value


def _count(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _digest(value: str | None, field_name: str, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class SanitizedEvidenceReport:
    """Typed, bounded summary of one already-run qualification dimension."""

    report_id: str
    status: CheckStatus
    summary: str
    command: str
    evidence_sha256: str | None = None
    critical_findings: int = 0
    important_findings: int = 0
    external_writes: int = 0
    provider_calls: int = 0
    model_downloads: int = 0
    rlm_work: int = 0
    secrets_exposed: int = 0

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.report_id) is None:
            raise ValueError("report_id is not safe")
        if self.status not in {"PASS", "NOT_APPLICABLE", "MANUAL_GATE", "BLOCKED"}:
            raise ValueError("report status is not closed")
        _text(self.summary, "summary")
        _text(self.command, "command")
        _digest(self.evidence_sha256, "evidence_sha256", required=self.status == "PASS")
        for name in (
            "critical_findings",
            "important_findings",
            "external_writes",
            "provider_calls",
            "model_downloads",
            "rlm_work",
            "secrets_exposed",
        ):
            _count(getattr(self, name), name)

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "critical_findings": self.critical_findings,
            "evidence_sha256": self.evidence_sha256,
            "external_writes": self.external_writes,
            "important_findings": self.important_findings,
            "model_downloads": self.model_downloads,
            "provider_calls": self.provider_calls,
            "report_id": self.report_id,
            "rlm_work": self.rlm_work,
            "secrets_exposed": self.secrets_exposed,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class ReleaseEnvironment:
    """Trusted, non-secret environment facts supplied by the release runner."""

    clean_tree: bool
    release_commit: str
    phase_tags: tuple[tuple[str, str], ...]
    migration_head: str
    external_writes: int = 0
    provider_calls: int = 0
    model_downloads: int = 0
    rlm_work: int = 0
    secrets_exposed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.clean_tree, bool):
            raise TypeError("clean_tree must be bool")
        if _COMMIT.fullmatch(self.release_commit) is None:
            raise ValueError("release_commit must be a full commit SHA")
        if not self.phase_tags:
            raise ValueError("phase_tags are required")
        for name, target in self.phase_tags:
            if (
                _SAFE_ID.fullmatch(name) is None
                or _text(target, "phase tag target", maximum=128) != target
            ):
                raise ValueError("phase tag is not bounded")
        _text(self.migration_head, "migration_head", maximum=128)
        for name in (
            "external_writes",
            "provider_calls",
            "model_downloads",
            "rlm_work",
            "secrets_exposed",
        ):
            _count(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ReleaseCheck:
    check_id: str
    status: CheckStatus
    evidence_ref: str
    evidence_sha256: str | None
    reason: str

    def __post_init__(self) -> None:
        if _SAFE_ID.fullmatch(self.check_id) is None:
            raise ValueError("check_id is not safe")
        if self.status not in {"PASS", "NOT_APPLICABLE", "MANUAL_GATE", "BLOCKED"}:
            raise ValueError("check status is not closed")
        _text(self.evidence_ref, "evidence_ref", maximum=256)
        _digest(self.evidence_sha256, "evidence_sha256", required=False)
        _text(self.reason, "reason")

    def as_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class Phase9ReleaseChecklistResult:
    checks: tuple[ReleaseCheck, ...]
    critical_failures: int
    important_findings: int
    external_writes: int
    secrets_exposed: int
    ready_for_human_acceptance: bool

    def __post_init__(self) -> None:
        for name in (
            "critical_failures",
            "important_findings",
            "external_writes",
            "secrets_exposed",
        ):
            _count(getattr(self, name), name)
        if not isinstance(self.ready_for_human_acceptance, bool):
            raise TypeError("ready_for_human_acceptance must be bool")

    def as_dict(self) -> dict[str, object]:
        return {
            "checks": tuple(check.as_dict() for check in self.checks),
            "critical_failures": self.critical_failures,
            "external_writes": self.external_writes,
            "important_findings": self.important_findings,
            "ready_for_human_acceptance": self.ready_for_human_acceptance,
            "secrets_exposed": self.secrets_exposed,
        }


def _check(
    check_id: str,
    status: CheckStatus,
    *,
    evidence_ref: str,
    evidence_sha256: str | None,
    reason: str,
) -> ReleaseCheck:
    return ReleaseCheck(check_id, status, evidence_ref, evidence_sha256, reason)


def run_release_checklist(
    *,
    manifest: Phase9ReleaseManifest,
    reports: tuple[SanitizedEvidenceReport, ...],
    environment: ReleaseEnvironment,
) -> Phase9ReleaseChecklistResult:
    """Evaluate all release dimensions without assuming missing evidence passes."""

    if not isinstance(manifest, Phase9ReleaseManifest):
        raise TypeError("manifest must be a Phase9ReleaseManifest")
    if not isinstance(environment, ReleaseEnvironment):
        raise TypeError("environment must be a ReleaseEnvironment")
    if not isinstance(reports, tuple) or not all(
        isinstance(report, SanitizedEvidenceReport) for report in reports
    ):
        raise TypeError("reports must be a tuple of SanitizedEvidenceReport")
    report_map: dict[str, SanitizedEvidenceReport] = {}
    for report in reports:
        if report.report_id in report_map:
            raise ValueError("duplicate evidence report")
        if report.report_id not in REQUIRED_REPORT_IDS:
            raise ValueError("unknown evidence report")
        report_map[report.report_id] = report

    checks: list[ReleaseCheck] = []
    checks.append(
        _check(
            "git_tree",
            "PASS"
            if environment.clean_tree and environment.release_commit == manifest.release_commit
            else "BLOCKED",
            evidence_ref="environment.git_tree",
            evidence_sha256=None,
            reason=(
                "clean tree and release commit match manifest"
                if environment.clean_tree and environment.release_commit == manifest.release_commit
                else "clean tree or release commit does not match the manifest"
            ),
        )
    )
    expected_tags = dict(manifest.phase_tags)
    actual_tags = dict(environment.phase_tags)
    tags_ok = actual_tags == expected_tags
    checks.append(
        _check(
            "phase_tags",
            "PASS" if tags_ok else "BLOCKED",
            evidence_ref="environment.phase_tags",
            evidence_sha256=None,
            reason="frozen phase tags match manifest"
            if tags_ok
            else "phase tag targets do not match manifest",
        )
    )
    migration_ok = environment.migration_head == EXPECTED_MIGRATION_HEAD == manifest.migration_head
    checks.append(
        _check(
            "migration_head",
            "PASS" if migration_ok else "BLOCKED",
            evidence_ref="environment.migration_head",
            evidence_sha256=None,
            reason="expected migration head is present"
            if migration_ok
            else "migration head is not the frozen head",
        )
    )

    for report_id in REQUIRED_REPORT_IDS:
        candidate_report = report_map.get(report_id)
        if candidate_report is None:
            checks.append(
                _check(
                    report_id,
                    "BLOCKED",
                    evidence_ref=f"reports/{report_id}.json",
                    evidence_sha256=None,
                    reason="required evidence report is missing",
                )
            )
        else:
            checks.append(
                _check(
                    candidate_report.report_id,
                    candidate_report.status,
                    evidence_ref=f"reports/{candidate_report.report_id}.json",
                    evidence_sha256=candidate_report.evidence_sha256,
                    reason=candidate_report.summary,
                )
            )

    totals = {
        name: getattr(environment, name) + sum(getattr(report, name) for report in reports)
        for name in (
            "external_writes",
            "provider_calls",
            "model_downloads",
            "rlm_work",
            "secrets_exposed",
        )
    }
    accepted_statuses = {"PASS", "NOT_APPLICABLE"}
    critical_failures = sum(report.critical_findings for report in reports)
    important_findings = sum(report.important_findings for report in reports)
    ready = (
        all(check.status in accepted_statuses for check in checks)
        and all(value == 0 for value in totals.values())
        and critical_failures == 0
        and important_findings == 0
        and all(report_id in report_map for report_id in REQUIRED_REPORT_IDS)
    )
    return Phase9ReleaseChecklistResult(
        checks=tuple(checks),
        critical_failures=critical_failures,
        important_findings=important_findings,
        external_writes=totals["external_writes"],
        secrets_exposed=totals["secrets_exposed"],
        ready_for_human_acceptance=ready,
    )


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    manifest_sha256: str
    report_hashes: tuple[tuple[str, str], ...]
    paths: tuple[str, ...]
    index_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _digest(self.manifest_sha256, "manifest_sha256", required=True)
        if tuple(sorted(self.report_hashes)) != self.report_hashes:
            raise ValueError("report hashes must be sorted")
        for report_id, digest in self.report_hashes:
            if _SAFE_ID.fullmatch(report_id) is None:
                raise ValueError("report hash id is not safe")
            _digest(digest, "report hash", required=True)
        if tuple(sorted(self.paths)) != self.paths or not all(
            path.endswith(".json") for path in self.paths
        ):
            raise ValueError("evidence paths are not canonical")
        object.__setattr__(
            self,
            "index_sha256",
            hashlib.sha256(_canonical_json(self.as_dict(include_hash=False))).hexdigest(),
        )

    def as_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "manifest_sha256": self.manifest_sha256,
            "paths": self.paths,
            "report_hashes": dict(self.report_hashes),
        }
        if include_hash:
            result["index_sha256"] = self.index_sha256
        return result


def build_evidence_package(
    *,
    manifest: Phase9ReleaseManifest,
    reports: tuple[SanitizedEvidenceReport, ...],
    output_dir: Path,
) -> EvidenceIndex:
    """Write only typed, sanitized summaries with canonical hashes."""

    if not isinstance(manifest, Phase9ReleaseManifest):
        raise TypeError("manifest must be a Phase9ReleaseManifest")
    if not isinstance(output_dir, Path):
        raise TypeError("output_dir must be a Path")
    if not isinstance(reports, tuple) or not all(
        isinstance(report, SanitizedEvidenceReport) for report in reports
    ):
        raise TypeError("reports must be a tuple of SanitizedEvidenceReport")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("evidence output directory is not empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    hashes: list[tuple[str, str]] = []
    paths: list[str] = []
    for report in sorted(reports, key=lambda item: item.report_id):
        if report.report_id in seen:
            raise ValueError("duplicate evidence report")
        seen.add(report.report_id)
        payload = _canonical_json(report.as_dict())
        relative = f"{report.report_id}.json"
        _atomic_write(output_dir / relative, payload)
        hashes.append((report.report_id, hashlib.sha256(payload).hexdigest()))
        paths.append(relative)
    index = EvidenceIndex(manifest.sha256, tuple(hashes), tuple(paths))
    _atomic_write(output_dir / "index.json", _canonical_json(index.as_dict()))
    return index


__all__ = [
    "EvidenceIndex",
    "REQUIRED_REPORT_IDS",
    "ReleaseCheck",
    "ReleaseEnvironment",
    "Phase9ReleaseChecklistResult",
    "SanitizedEvidenceReport",
    "build_evidence_package",
    "run_release_checklist",
]
