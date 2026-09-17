from __future__ import annotations

import pytest
from pydantic import ValidationError

from aaos.rag.contracts import RagAnswerStatus
from aaos.web.contracts import (
    RagAnswerResponse,
    RagCitation,
    RagCourseScopeRequest,
    RagGlobalScopeRequest,
    RagQueryRequest,
)


def test_rag_request_accepts_only_bounded_question_and_closed_scopes() -> None:
    global_request = RagQueryRequest(
        question="Explain self-attention.",
        scope=RagGlobalScopeRequest(type="global"),
    )
    course_request = RagQueryRequest(
        question="Summarize the syllabus.",
        scope=RagCourseScopeRequest(type="course", course_id=12),
    )

    assert global_request.question == "Explain self-attention."
    assert course_request.scope.course_id == 12


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "", "scope": {"type": "global"}},
        {"question": "x" * 4097, "scope": {"type": "global"}},
        {"question": "question", "scope": {"type": "course", "course_id": 0}},
        {"question": "question", "scope": {"type": "course", "course_id": -1}},
        {"question": "question", "scope": {"type": "historical", "document_version_id": 1}},
        {"question": "question", "scope": {"type": "global", "provider": "secret"}},
        {"question": "question", "scope": {"type": "global"}, "model": "local"},
        {"question": 12, "scope": {"type": "global"}},
    ],
)
def test_rag_request_rejects_invalid_or_authority_bearing_input(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RagQueryRequest.model_validate(payload)


def test_rag_models_are_strict_frozen_and_closed() -> None:
    request = RagQueryRequest(
        question="question",
        scope=RagGlobalScopeRequest(type="global"),
    )
    with pytest.raises(ValidationError):
        request.question = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RagCitation.model_validate(
            {
                "document_id": 1,
                "document_title": "Notes",
                "page_start": 1,
                "page_end": 1,
                "document_version_id": 99,
            }
        )


def test_rag_answer_response_has_exact_safe_projection() -> None:
    answer = RagAnswerResponse(
        status=RagAnswerStatus.ANSWER,
        answer_text="A validated answer.",
        language="en",
        citations=(
            RagCitation(
                document_id=3,
                document_title="Notes",
                page_start=2,
                page_end=4,
            ),
        ),
    )
    insufficient = RagAnswerResponse(
        status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
        answer_text="There is not enough evidence.",
        language="en",
        citations=(),
    )

    assert set(answer.model_dump()) == {"status", "answer_text", "language", "citations"}
    assert answer.citations[0].model_dump() == {
        "document_id": 3,
        "document_title": "Notes",
        "page_start": 2,
        "page_end": 4,
    }
    assert insufficient.citations == ()

