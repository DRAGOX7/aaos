"""Frozen, synthetic-only Phase 9 evaluation membership."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

PHASE9_EVALUATION_SCHEMA_VERSION = "phase9-evaluation-v1"
DOMAINS = (
    "retrieval",
    "grounding_nli",
    "citation",
    "agent_tool",
    "multimodal",
    "computation",
)
CALIBRATION_IDS = (
    "cal-retrieval-en-001",
    "cal-retrieval-en-002",
    "cal-retrieval-en-003",
    "cal-retrieval-en-004",
    "cal-retrieval-ar-001",
    "cal-retrieval-ar-002",
    "cal-retrieval-ar-003",
    "cal-retrieval-ar-004",
    "cal-grounding-nli-en-001",
    "cal-grounding-nli-ar-001",
    "cal-citation-en-001",
    "cal-citation-ar-001",
    "cal-agent-tool-en-001",
    "cal-agent-tool-ar-001",
    "cal-multimodal-en-001",
    "cal-multimodal-ar-001",
    "cal-computation-en-001",
    "cal-computation-ar-001",
)
HELDOUT_IDS = (
    "holdout-retrieval-en-001",
    "holdout-retrieval-ar-001",
    "holdout-grounding-nli-en-001",
    "holdout-grounding-nli-ar-001",
    "holdout-citation-en-001",
    "holdout-citation-ar-001",
    "holdout-multimodal-en-001",
    "holdout-multimodal-ar-001",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_REAL_HELDOUT_ID = re.compile(r"^hold-[0-9]{3}$")


def _canonical_bytes(
    schema_version: str,
    calibration_ids: tuple[str, ...],
    heldout_ids: tuple[str, ...],
    domains: tuple[str, ...],
) -> bytes:
    return json.dumps(
        {
            "calibration_ids": calibration_ids,
            "domains": domains,
            "heldout_ids": heldout_ids,
            "schema_version": schema_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(
    schema_version: str,
    calibration_ids: tuple[str, ...],
    heldout_ids: tuple[str, ...],
    domains: tuple[str, ...],
) -> str:
    return hashlib.sha256(
        _canonical_bytes(schema_version, calibration_ids, heldout_ids, domains)
    ).hexdigest()


def _language(case_id: str) -> str:
    if "-en-" in case_id:
        return "en"
    if "-ar-" in case_id:
        return "ar"
    raise ValueError("evaluation case must declare an en or ar language suffix")


@dataclass(frozen=True, slots=True)
class Phase9EvaluationManifest:
    schema_version: str
    calibration_ids: tuple[str, ...]
    heldout_ids: tuple[str, ...]
    domains: tuple[str, ...]
    sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != PHASE9_EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported Phase 9 evaluation schema")
        if not isinstance(self.calibration_ids, tuple) or not isinstance(self.heldout_ids, tuple):
            raise TypeError("manifest IDs must be tuples")
        if not isinstance(self.domains, tuple) or self.domains != DOMAINS:
            raise ValueError("manifest domains are not the closed Phase 9 set")
        for values in (self.calibration_ids, self.heldout_ids):
            if not values or len(set(values)) != len(values):
                raise ValueError("manifest IDs must be non-empty and unique")
            if not all(isinstance(value, str) and _SAFE_ID.fullmatch(value) for value in values):
                raise ValueError("manifest IDs must be bounded safe identifiers")
            if not all(_language(value) in {"en", "ar"} for value in values):
                raise ValueError("manifest IDs must include language metadata")
        if set(self.calibration_ids) & set(self.heldout_ids):
            raise ValueError("calibration and heldout IDs must be disjoint")
        if any(_REAL_HELDOUT_ID.fullmatch(value) for value in self.calibration_ids):
            raise ValueError("real heldout IDs cannot enter calibration")
        calibration_counts = self.language_counts
        heldout_counts = self.heldout_language_counts
        if calibration_counts["en"] != calibration_counts["ar"]:
            raise ValueError("calibration and heldout language partitions must be balanced")
        if heldout_counts["en"] != heldout_counts["ar"]:
            raise ValueError("calibration and heldout language partitions must be balanced")
        if self.sha256 != _digest(
            self.schema_version, self.calibration_ids, self.heldout_ids, self.domains
        ):
            raise ValueError("manifest sha256 does not match frozen membership")

    @property
    def language_counts(self) -> dict[str, int]:
        values = self.calibration_ids
        return {"en": sum(_language(value) == "en" for value in values), "ar": sum(_language(value) == "ar" for value in values)}

    @property
    def heldout_language_counts(self) -> dict[str, int]:
        values = self.heldout_ids
        return {"en": sum(_language(value) == "en" for value in values), "ar": sum(_language(value) == "ar" for value in values)}

    @classmethod
    def from_ids(
        cls,
        calibration_ids: tuple[str, ...],
        heldout_ids: tuple[str, ...],
        *,
        domains: tuple[str, ...] = DOMAINS,
    ) -> "Phase9EvaluationManifest":
        schema_version = PHASE9_EVALUATION_SCHEMA_VERSION
        return cls(
            schema_version,
            calibration_ids,
            heldout_ids,
            domains,
            _digest(schema_version, calibration_ids, heldout_ids, domains),
        )


def build_manifest() -> Phase9EvaluationManifest:
    return Phase9EvaluationManifest.from_ids(CALIBRATION_IDS, HELDOUT_IDS)


__all__ = [
    "CALIBRATION_IDS",
    "DOMAINS",
    "HELDOUT_IDS",
    "PHASE9_EVALUATION_SCHEMA_VERSION",
    "Phase9EvaluationManifest",
    "build_manifest",
]
