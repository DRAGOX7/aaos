from __future__ import annotations

from aaos.rag.contracts import RagAnswerStatus
from aaos.web.contracts import RagAnswerResponse, RagCitation


def test_browser_rag_projection_excludes_internal_provenance_and_authority_fields() -> None:
    response = RagAnswerResponse(
        status=RagAnswerStatus.ANSWER,
        answer_text="Validated answer",
        language="en",
        citations=(
            RagCitation(
                document_id=7,
                document_title="Notes",
                page_start=1,
                page_end=1,
            ),
        ),
    )

    serialized = response.model_dump()
    assert set(serialized) == {"status", "answer_text", "language", "citations"}
    assert "document_version_id" not in str(serialized)
    assert "provider" not in str(serialized).lower()
    assert "permission" not in str(serialized).lower()

