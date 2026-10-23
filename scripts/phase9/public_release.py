"""Bounded demo claims and public-release scanning for Phase 9."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ClaimScope = Literal["REAL_LOCAL", "DETERMINISTIC_FAKE", "HISTORICAL_EXTERNAL", "NOT_SUPPORTED"]
DemoLabel = Literal["REAL_LOCAL_BEHAVIOR", "DETERMINISTIC_FAKE_BEHAVIOR", "SYNTHETIC_DATA"]
_SECRET_PATTERN = re.compile(
    rb"(?i)(?:client_secret|access_token|refresh_token|private_key|authorization)\s*[:=]"
)
# A token stored as JSON ("refresh_token": "<long value>") and a bare JWT (an ID token,
# for one): the shapes a saved sign-in takes, which the pattern above does not see.
_STORED_TOKEN_PATTERN = re.compile(
    rb'(?i)"(?:access_token|refresh_token|id_token|client_secret)"\s*:\s*"[^"\s]{20,}"'
)
_JWT_PATTERN = re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_PRIVATE_PATH_PARTS = frozenset({"university", "private", "personal"})
_MODEL_SUFFIXES = frozenset({".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".bin"})
_TEMP_SUFFIXES = frozenset({".har", ".netlog", ".trace", ".png", ".jpg", ".jpeg"})


@dataclass(frozen=True, slots=True)
class PortfolioClaim:
    claim_id: str
    text: str
    evidence_refs: tuple[str, ...]
    scope: ClaimScope

    def __post_init__(self) -> None:
        if not self.claim_id or len(self.claim_id) > 96 or not self.text or len(self.text) > 512:
            raise ValueError("claim fields are not bounded")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("claim requires evidence references")
        if not all(isinstance(item, str) and 0 < len(item) <= 128 for item in self.evidence_refs):
            raise ValueError("claim evidence references are not bounded")
        if self.scope not in {
            "REAL_LOCAL",
            "DETERMINISTIC_FAKE",
            "HISTORICAL_EXTERNAL",
            "NOT_SUPPORTED",
        }:
            raise ValueError("claim scope is not closed")


@dataclass(frozen=True, slots=True)
class DemoStep:
    step_id: str
    label: DemoLabel
    action: str
    expected_observation: str
    external_writes: int

    def __post_init__(self) -> None:
        if not self.step_id or len(self.step_id) > 96:
            raise ValueError("step_id is not bounded")
        if self.label not in {
            "REAL_LOCAL_BEHAVIOR",
            "DETERMINISTIC_FAKE_BEHAVIOR",
            "SYNTHETIC_DATA",
        }:
            raise ValueError("demo label is not closed")
        if not self.action or len(self.action) > 512 or not self.expected_observation:
            raise ValueError("demo text is not bounded")
        if self.external_writes != 0:
            raise ValueError("Phase 9 demo cannot perform external writes")


def build_demo_steps() -> tuple[DemoStep, ...]:
    return (
        DemoStep(
            "dashboard",
            "REAL_LOCAL_BEHAVIOR",
            "Open the local dashboard",
            "Same-origin dashboard renders",
            0,
        ),
        DemoStep(
            "rag",
            "REAL_LOCAL_BEHAVIOR",
            "Run a grounded RAG query",
            "Citation-bearing local answer or insufficient evidence",
            0,
        ),
        DemoStep(
            "visual",
            "REAL_LOCAL_BEHAVIOR",
            "Inspect derived visual evidence",
            "Visual provenance remains distinct from embedded PDF text",
            0,
        ),
        DemoStep(
            "computation",
            "DETERMINISTIC_FAKE_BEHAVIOR",
            "Run restricted computation fixture",
            "Fake sandbox result is labeled and bounded",
            0,
        ),
        DemoStep(
            "approval",
            "DETERMINISTIC_FAKE_BEHAVIOR",
            "Review fake approval lifecycle",
            "Approval and UNKNOWN_OUTCOME remain trusted-core states",
            0,
        ),
        DemoStep(
            "activity",
            "REAL_LOCAL_BEHAVIOR",
            "Open Activity history",
            "Deterministic local events render",
            0,
        ),
        DemoStep(
            "health",
            "REAL_LOCAL_BEHAVIOR",
            "Open passive System Health",
            "Health status is observational",
            0,
        ),
        DemoStep(
            "synthetic",
            "SYNTHETIC_DATA",
            "Use sanitized demo fixtures",
            "No private university data is shown",
            0,
        ),
    )


@dataclass(frozen=True, slots=True)
class PublicReleaseReport:
    scanned_paths: int
    secret_findings: int
    private_data_findings: int
    normal_database_findings: int
    model_weight_findings: int
    temporary_artifact_findings: int
    raw_matches: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_paths": self.scanned_paths,
            "secret_findings": self.secret_findings,
            "private_data_findings": self.private_data_findings,
            "normal_database_findings": self.normal_database_findings,
            "model_weight_findings": self.model_weight_findings,
            "temporary_artifact_findings": self.temporary_artifact_findings,
            "raw_matches": self.raw_matches,
        }


def _candidate_files(root: Path) -> tuple[Path, ...]:
    ignored = {".git", ".venv", "node_modules", "__pycache__", ".next"}
    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts)
    )


def scan_public_release(repo_root: Path) -> PublicReleaseReport:
    if not isinstance(repo_root, Path):
        raise TypeError("repo_root must be a Path")
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    secret = private = database = weights = temporary = 0
    paths = _candidate_files(repo_root)
    for path in paths:
        relative_parts = {part.lower() for part in path.relative_to(repo_root).parts}
        lower_name = path.name.lower()
        if relative_parts & {"credentials", "auth"} or lower_name.startswith(".env"):
            secret += 1
        if relative_parts & _PRIVATE_PATH_PARTS:
            private += 1
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            database += 1
        if path.suffix.lower() in _MODEL_SUFFIXES:
            weights += 1
        if path.suffix.lower() in _TEMP_SUFFIXES or "screenshot" in lower_name:
            temporary += 1
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if (
            _SECRET_PATTERN.search(payload)
            or _STORED_TOKEN_PATTERN.search(payload)
            or _JWT_PATTERN.search(payload)
        ):
            secret += 1
    return PublicReleaseReport(len(paths), secret, private, database, weights, temporary)


__all__ = [
    "DemoStep",
    "PortfolioClaim",
    "PublicReleaseReport",
    "build_demo_steps",
    "scan_public_release",
]
