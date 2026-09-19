"""Trusted Phase 8 computation adapter and model-safe projection."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Protocol

from aaos.agent.contracts import ToolObservation

from .computation_contracts import (
    ComputationInput,
    ComputationRequest,
    ComputationResult,
    ComputationStatus,
)
from .visual_provenance import VisualAnalysis


class ComputationPermission(StrEnum):
    """Closed trusted capability decision; never model-selectable."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


class ComputationToolAdapter(Protocol):
    def execute(self, request: ComputationRequest) -> ComputationResult: ...


def build_visual_computation_input(analysis: VisualAnalysis) -> ComputationInput:
    """Project visual evidence as bounded JSON data for trusted computation."""

    if not isinstance(analysis, VisualAnalysis):
        raise TypeError("analysis must be a VisualAnalysis")
    if analysis.status not in {"SUCCEEDED", "PARTIAL"}:
        raise ValueError("only usable visual analysis can be computed")
    payload = {
        "analysis_key": analysis.analysis_key,
        "analysis_schema_revision": analysis.analysis_schema_revision,
        "document_version_id": analysis.document_version_id,
        "page_number": analysis.page_number,
        "source_content_sha256": analysis.source_content_sha256,
        "render_sha256": analysis.render_sha256,
        "model_id": analysis.model_id,
        "model_revision": analysis.model_revision,
        "prompt_revision": analysis.prompt_revision,
        "selector_revision": analysis.selector_revision,
        "renderer_revision": analysis.renderer_revision,
        "status": analysis.status.value,
        "elements": [
            {
                "sequence": sequence,
                "kind": element.kind.value,
                "observed_text": element.observed_text,
                "interpretation_text": element.interpretation_text,
                "structured_payload": element.structured_payload,
                "payload_sha256": element.payload_sha256,
                "confidence": element.confidence,
            }
            for sequence, element in enumerate(analysis.elements, start=1)
        ],
    }
    bounded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ComputationInput(
        input_id=f"visual-{analysis.analysis_key}",
        media_type="application/json",
        content_sha256=hashlib.sha256(bounded).hexdigest(),
        bounded_bytes=bounded,
    )


def project_computation_observation(result: ComputationResult) -> ToolObservation:
    """Project one bounded result without forwarding worker text or authority."""

    if not isinstance(result, ComputationResult):
        raise TypeError("result must be a ComputationResult")
    if result.status is ComputationStatus.SUCCEEDED:
        return ToolObservation(
            kind="phase8_computation",
            status="OK",
            text="computation completed",
            fields=(("duration_ms", result.duration_ms), ("status", result.status.value)),
        )
    messages = {
        ComputationStatus.FAILED: "computation failed",
        ComputationStatus.TIMED_OUT: "computation timed out",
        ComputationStatus.CANCELLED: "computation cancelled",
        ComputationStatus.UNKNOWN_OUTCOME: "computation outcome is unknown",
    }
    return ToolObservation(
        kind="phase8_computation",
        status="ERROR",
        text=messages[result.status],
        fields=(("duration_ms", result.duration_ms), ("status", result.status.value)),
    )


__all__ = [
    "ComputationPermission",
    "ComputationToolAdapter",
    "build_visual_computation_input",
    "project_computation_observation",
]
