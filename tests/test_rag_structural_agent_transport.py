from __future__ import annotations

import pytest

from aaos.agent.contracts import ToolResult
from aaos.agent.phase4_adapter import (
    _citation_payload,
    rag_result_to_observation,
)
from aaos.rag.contracts import (
    ProvenanceKind,
    StructuralCitation,
)


def _citation() -> StructuralCitation:
    return StructuralCitation(
        claim_id="claim-struct",
        document_version_id=101,
        document_title="notes.docx",
        unit_start=4,
        unit_end=4,
        heading="Neural Networks",
        table_index=None,
        row_index=None,
        column_index=None,
        provenance_kind=ProvenanceKind.STRUCTURAL_DOCUMENT,
    )


def _payload() -> dict[str, object]:
    return {
        "claim_id": "claim-struct",
        "document_version_id": 101,
        "document_title": "notes.docx",
        "unit_start": 4,
        "unit_end": 4,
        "heading": "Neural Networks",
        "table_index": None,
        "row_index": None,
        "column_index": None,
    }


def test_agent_adapter_serializes_structural_citation_without_pages() -> None:
    payload = _citation_payload(_citation())

    assert payload == _payload()
    assert "page_start" not in payload
    assert "page_end" not in payload


def test_agent_observation_accepts_structural_citation_payload() -> None:
    result = ToolResult.success(
        {
            "status": "ANSWER",
            "answer_text": "Neural networks are covered.",
            "language": "en",
            "rendered_text": (
                "Neural networks are covered.\n\n"
                "[claim-struct] notes.docx "
                "(unit 4; heading Neural Networks)"
            ),
            "citations": (_payload(),),
            "generation_id": 3,
            "content_revision": 7,
        }
    )

    observation = rag_result_to_observation(result)

    assert observation.status == "OK"

    fields = dict(observation.fields)
    citations = fields["citations"]

    assert isinstance(citations, tuple)
    assert len(citations) == 1

    citation = citations[0]
    assert citation == _payload()
    assert "page_start" not in citation
    assert "page_end" not in citation


def test_agent_observation_rejects_mixed_page_and_structure_shape() -> None:
    malformed = _payload()
    malformed["page_start"] = 1
    malformed["page_end"] = 1

    result = ToolResult.success(
        {
            "status": "ANSWER",
            "answer_text": "Neural networks are covered.",
            "language": "en",
            "rendered_text": "Neural networks are covered.",
            "citations": (malformed,),
            "generation_id": 3,
            "content_revision": 7,
        }
    )

    with pytest.raises(ValueError):
        rag_result_to_observation(result)
