"""Synthetic-only Phase 9 evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .manifest import Phase9EvaluationManifest
from .metrics import (
    Metric,
    NliConfusion,
    NliLabel,
    citation_accuracy,
    nli_confusion,
    recall_at_k,
    routing_domain_report,
    unsupported_claim_rate,
    visual_selection_report,
)


@dataclass(frozen=True, slots=True)
class CaseObservation:
    model_calls: int = 0
    rag_calls: int = 0
    external_writes: int = 0


class EvaluationAdapter(Protocol):
    def evaluate(self, case_id: str) -> CaseObservation: ...


@dataclass(frozen=True, slots=True)
class DomainReport:
    name: str
    case_count: int
    metrics: tuple[Metric, ...]
    nli: NliConfusion | None = None
    provenance_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Phase9EvaluationReport:
    manifest_sha256: str
    domain_reports: tuple[DomainReport, ...]
    model_calls: int
    rag_calls: int
    external_writes: int
    fixture_mutations: int
    blended_primary_score: None = None


class EvaluationAdapters:
    """Default deterministic adapters; no model, RAG, or provider is called."""

    def evaluate(self, case_id: str) -> CaseObservation:
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case_id must be a bounded identifier")
        return CaseObservation()


def _domain_case_ids(manifest: Phase9EvaluationManifest, prefix: str) -> tuple[str, ...]:
    return tuple(case_id for case_id in manifest.calibration_ids if prefix in case_id)


def run_deterministic_evaluation(
    manifest: Phase9EvaluationManifest,
    *,
    adapters: EvaluationAdapter | None = None,
) -> Phase9EvaluationReport:
    if not isinstance(manifest, Phase9EvaluationManifest):
        raise TypeError("manifest must be a Phase9EvaluationManifest")
    if any(case_id.startswith("hold-") for case_id in manifest.calibration_ids):
        raise ValueError("real heldout IDs cannot be executed by normal evaluation")
    adapter = adapters or EvaluationAdapters()
    observations = tuple(adapter.evaluate(case_id) for case_id in manifest.calibration_ids)
    if not all(isinstance(item, CaseObservation) for item in observations):
        raise TypeError("evaluation adapters must return CaseObservation")
    domain_reports = (
        DomainReport(
            "retrieval",
            len(_domain_case_ids(manifest, "retrieval")),
            (
                recall_at_k(
                    _domain_case_ids(manifest, "retrieval"),
                    _domain_case_ids(manifest, "retrieval"),
                    k=len(_domain_case_ids(manifest, "retrieval")),
                ),
            ),
        ),
        DomainReport(
            "grounding_nli",
            len(_domain_case_ids(manifest, "grounding-nli")),
            (unsupported_claim_rate(unsupported=0, total=2),),
            nli=nli_confusion(
                (NliLabel.ENTAILMENT, NliLabel.NEUTRAL, NliLabel.CONTRADICTION),
                (NliLabel.ENTAILMENT, NliLabel.NEUTRAL, NliLabel.CONTRADICTION),
            ),
        ),
        DomainReport(
            "citation",
            len(_domain_case_ids(manifest, "citation")),
            (citation_accuracy(correct=2, total=2),),
        ),
        DomainReport(
            "agent_tool",
            len(_domain_case_ids(manifest, "agent-tool")),
            (routing_domain_report(_domain_case_ids(manifest, "agent-tool"), correct=len(_domain_case_ids(manifest, "agent-tool"))),),
        ),
        DomainReport(
            "multimodal",
            len(_domain_case_ids(manifest, "multimodal")),
            (
                visual_selection_report(
                    selected=2,
                    expected=2,
                    provenance_kinds=("DERIVED_VISUAL_EVIDENCE", "EMBEDDED_PDF"),
                ).metric,
            ),
            provenance_kinds=("DERIVED_VISUAL_EVIDENCE", "EMBEDDED_PDF"),
        ),
        DomainReport(
            "computation",
            len(_domain_case_ids(manifest, "computation")),
            (routing_domain_report(_domain_case_ids(manifest, "computation"), correct=len(_domain_case_ids(manifest, "computation"))),),
        ),
    )
    return Phase9EvaluationReport(
        manifest_sha256=manifest.sha256,
        domain_reports=domain_reports,
        model_calls=sum(item.model_calls for item in observations),
        rag_calls=sum(item.rag_calls for item in observations),
        external_writes=sum(item.external_writes for item in observations),
        fixture_mutations=0,
    )


__all__ = [
    "CaseObservation",
    "DomainReport",
    "EvaluationAdapters",
    "EvaluationAdapter",
    "Phase9EvaluationReport",
    "run_deterministic_evaluation",
]
