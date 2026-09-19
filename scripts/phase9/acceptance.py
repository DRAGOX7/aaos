"""Pure final Phase 9 freeze-readiness validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from scripts.phase9.release_check import EvidenceIndex
from scripts.phase9.release_manifest import EXPECTED_MIGRATION_HEAD, EXPECTED_PHASE8_TARGET

HumanDecision = Literal["PENDING", "ACCEPTED"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _head(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a full commit SHA")


def _digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")


def _nonnegative(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class Phase9AcceptanceInputs:
    starting_head: str
    final_head: str
    expected_release_head: str
    phase8_tag_target: str
    migration_head: str
    manifest_sha256: str
    evidence_index: EvidenceIndex
    test_totals: tuple[tuple[str, str], ...]
    clean_tree: bool
    manual_gates_complete: bool
    cleanup_ok: bool
    external_writes: int
    model_downloads: int
    provider_calls: int
    rlm_work: int
    critical_findings: int
    important_findings: int
    unresolved_secret_findings: int
    human_decision: HumanDecision | None

    def __post_init__(self) -> None:
        _head(self.starting_head, "starting_head")
        _head(self.final_head, "final_head")
        _head(self.expected_release_head, "expected_release_head")
        _digest(self.manifest_sha256, "manifest_sha256")
        if not isinstance(self.evidence_index, EvidenceIndex):
            raise TypeError("evidence_index must be an EvidenceIndex")
        if not isinstance(self.migration_head, str) or not self.migration_head:
            raise ValueError("migration_head is required")
        if self.human_decision not in {None, "PENDING", "ACCEPTED"}:
            raise ValueError("human_decision is not closed")
        for name in ("clean_tree", "manual_gates_complete", "cleanup_ok"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        for name in (
            "external_writes",
            "model_downloads",
            "provider_calls",
            "rlm_work",
            "critical_findings",
            "important_findings",
            "unresolved_secret_findings",
        ):
            _nonnegative(getattr(self, name), name)
        names: set[str] = set()
        for name, value in self.test_totals:
            if not name or name in names or not value.isdigit():
                raise ValueError("test_totals must contain unique numeric facts")
            names.add(name)


@dataclass(frozen=True, slots=True)
class Phase9AcceptanceReport:
    starting_head: str
    final_head: str
    phase8_tag_target: str
    migration_head: str
    manifest_sha256: str
    evidence_index_sha256: str | None
    test_totals: tuple[tuple[str, str], ...]
    external_writes: int
    model_downloads: int
    provider_calls: int
    rlm_work: int
    cleanup_ok: bool
    clean_tree: bool
    human_accepted: bool
    ready_for_human_freeze_acceptance: bool
    blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "blockers": self.blockers,
            "clean_tree": self.clean_tree,
            "cleanup_ok": self.cleanup_ok,
            "evidence_index_sha256": self.evidence_index_sha256,
            "external_writes": self.external_writes,
            "final_head": self.final_head,
            "human_accepted": self.human_accepted,
            "manifest_sha256": self.manifest_sha256,
            "migration_head": self.migration_head,
            "model_downloads": self.model_downloads,
            "phase8_tag_target": self.phase8_tag_target,
            "provider_calls": self.provider_calls,
            "ready_for_human_freeze_acceptance": self.ready_for_human_freeze_acceptance,
            "rlm_work": self.rlm_work,
            "starting_head": self.starting_head,
            "test_totals": dict(self.test_totals),
        }


def run_phase9_acceptance(inputs: Phase9AcceptanceInputs) -> Phase9AcceptanceReport:
    """Validate the existing release evidence without performing I/O."""

    if not isinstance(inputs, Phase9AcceptanceInputs):
        raise TypeError("inputs must be Phase9AcceptanceInputs")
    blockers: list[str] = []
    if inputs.starting_head != inputs.expected_release_head:
        blockers.append("starting HEAD does not match the release candidate")
    if not inputs.clean_tree:
        blockers.append("dirty tree")
    if inputs.phase8_tag_target != EXPECTED_PHASE8_TARGET:
        blockers.append("Phase 8 tag target mismatch")
    if inputs.migration_head != EXPECTED_MIGRATION_HEAD:
        blockers.append("migration head mismatch")
    evidence_index_sha = inputs.evidence_index.index_sha256
    if inputs.evidence_index.manifest_sha256 != inputs.manifest_sha256:
        blockers.append("manifest hash does not match evidence index")
    if not inputs.evidence_index.report_hashes:
        blockers.append("evidence report hash is missing")
    if inputs.human_decision is None:
        blockers.append("human decision is missing")
    if not inputs.manual_gates_complete:
        blockers.append("manual gate evidence is incomplete")
    if not inputs.cleanup_ok:
        blockers.append("cleanup is incomplete")
    if inputs.external_writes:
        blockers.append("external writes are nonzero")
    if inputs.model_downloads:
        blockers.append("model downloads are nonzero")
    if inputs.provider_calls:
        blockers.append("provider calls are nonzero")
    if inputs.rlm_work:
        blockers.append("RLM work is nonzero")
    if inputs.critical_findings:
        blockers.append("Critical findings remain")
    if inputs.important_findings:
        blockers.append("Important findings remain")
    if inputs.unresolved_secret_findings:
        blockers.append("secret/privacy findings remain")
    return Phase9AcceptanceReport(
        starting_head=inputs.starting_head,
        final_head=inputs.final_head,
        phase8_tag_target=inputs.phase8_tag_target,
        migration_head=inputs.migration_head,
        manifest_sha256=inputs.manifest_sha256,
        evidence_index_sha256=evidence_index_sha,
        test_totals=inputs.test_totals,
        external_writes=inputs.external_writes,
        model_downloads=inputs.model_downloads,
        provider_calls=inputs.provider_calls,
        rlm_work=inputs.rlm_work,
        cleanup_ok=inputs.cleanup_ok,
        clean_tree=inputs.clean_tree,
        human_accepted=inputs.human_decision == "ACCEPTED" and not blockers,
        ready_for_human_freeze_acceptance=not blockers,
        blockers=tuple(blockers),
    )


__all__ = ["Phase9AcceptanceInputs", "Phase9AcceptanceReport", "run_phase9_acceptance"]
