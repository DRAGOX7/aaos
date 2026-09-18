"""Deterministic, non-production fixtures for Phase 7D acceptance tests."""

from __future__ import annotations

from dataclasses import dataclass

from aaos.rag.contracts import RagAnswerStatus
from aaos.web.contracts import RagAnswerResponse, RagAnswerStatusView, RagCitation


@dataclass(frozen=True, slots=True)
class Phase7dFixtureManifest:
    name: str = "phase7d-rag-fixture-v1"
    model_calls: int = 0
    writes: int = 0


def fixture_answer(status: RagAnswerStatus) -> RagAnswerResponse:
    citations = () if status is RagAnswerStatus.INSUFFICIENT_EVIDENCE else (
        RagCitation(
            document_id=7,
            document_title="Synthetic course notes",
            page_start=1,
            page_end=2,
        ),
    )
    return RagAnswerResponse(
        status=RagAnswerStatusView(status.value),
        answer_text=(
            "There is not enough evidence."
            if status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
            else "A validated synthetic answer."
        ),
        language="en",
        citations=citations,
    )
