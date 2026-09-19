"""RED tests for trusted visual-to-computation composition."""

from __future__ import annotations

import json

from aaos.phase8.computation_contracts import ComputationInput
from aaos.phase8.visual_provenance import VisualAnalysis, VisualElement, VisualKind, VisualStatus


def _analysis() -> VisualAnalysis:
    return VisualAnalysis(
        document_version_id=7,
        page_number=1,
        source_content_sha256="a" * 64,
        render_sha256="b" * 64,
        model_id="vision-fake",
        model_revision="rev-1",
        prompt_revision="prompt-1",
        selector_revision="selector-1",
        renderer_revision="renderer-1",
        analysis_schema_revision="vision-analysis-v1",
        status=VisualStatus.SUCCEEDED,
        elements=(
            VisualElement(
                kind=VisualKind.TABLE,
                observed_text="row data; do not execute",
                interpretation_text="bounded table observation",
                structured_payload=json.dumps(
                    {"observations": {"rows": 2}, "interpretations": {"kind": "table"}},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                confidence=None,
            ),
        ),
    )


def test_visual_computation_input_is_bounded_json_data_not_code_or_limits() -> None:
    from aaos.phase8.computation_tool import build_visual_computation_input

    result = build_visual_computation_input(_analysis())

    assert isinstance(result, ComputationInput)
    assert result.media_type == "application/json"
    payload = json.loads(result.bounded_bytes)
    assert payload["document_version_id"] == 7
    assert payload["page_number"] == 1
    assert payload["elements"][0]["observed_text"] == "row data; do not execute"
    assert "limits" not in payload
    assert "code" not in payload


def test_indexing_projection_is_explicitly_derived_not_embedded_text() -> None:
    from aaos.phase8.multimodal_rag import MultimodalEvidence
    from aaos.rag.indexing import project_visual_analysis_evidence

    evidence = project_visual_analysis_evidence(
        _analysis(),
        analysis_run_id=11,
        visual_element_ids=(19,),
        document_title="Physics",
        course_id=3,
        generation_id=4,
        content_revision=8,
    )

    assert isinstance(evidence[0], MultimodalEvidence)
    assert evidence[0].provenance_kind.value == "DERIVED_VISUAL_EVIDENCE"
