"""Archived (past) courses in the academic assistant (archived-courses spec)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from test_agent_academic_orchestration import (
    NOW,
    FakeAcademicReader,
    FakeDocumentReader,
    FakeGateway,
    FakeRagService,
    _rag_grounded_answer,
)

from aaos.agent.academic_orchestrator import AcademicAskStatus, ReadOnlyAcademicAssistant
from aaos.agent.intent_router import asks_about_past
from aaos.rag.contracts import CourseScope, GlobalScope, HistoricalScope
from aaos.web.contracts import CourseSummary

ARCHIVED = datetime(2026, 10, 2, 8, 16, tzinfo=timezone.utc)
PAST_SE440 = CourseSummary(
    id=5,
    title="SE440-PROJECT MANAGEMENT (All Sections )",
    code=None,
    description=None,
    updated_at=NOW,
    status="past",
    archived_at=ARCHIVED,
)


class VisibleDocuments(FakeDocumentReader):
    """Unscoped listings cover current courses only, like the real reader."""

    def __init__(self, past_course_ids: set[int]) -> None:
        super().__init__()
        self.past_course_ids = past_course_ids

    def list_documents(self, course_id: int | None = None, *, limit: int | None = None) -> Any:
        documents = super().list_documents(course_id, limit=limit)
        if course_id is None:
            return tuple(d for d in documents if d.course_id not in self.past_course_ids)
        return documents


def _world(*, current_documents: bool = True) -> tuple[FakeAcademicReader, VisibleDocuments]:
    academic = FakeAcademicReader()
    academic.courses = [*academic.courses, PAST_SE440]
    documents = VisibleDocuments({PAST_SE440.id})
    template = documents.documents[0]
    past_document = template.model_copy(
        update={
            "id": 31,
            "course_id": PAST_SE440.id,
            "title": "SE440 Syllabus.pdf",
            "document_version_id": 202,
        }
    )
    documents.documents = [*(documents.documents if current_documents else []), past_document]
    return academic, documents


def _assistant(
    academic: FakeAcademicReader, documents: FakeDocumentReader
) -> tuple[ReadOnlyAcademicAssistant, FakeGateway, FakeRagService]:
    gateway = FakeGateway()
    rag = FakeRagService(_rag_grounded_answer())
    assistant = ReadOnlyAcademicAssistant(
        gateway, academic, documents, rag, clock=lambda: NOW, intent_routing=True
    )
    return assistant, gateway, rag


def test_past_wording_is_recognized_in_english_and_arabic() -> None:
    assert asks_about_past("What did my previous courses say about makeup exams?")
    assert asks_about_past("What courses did I take last semester?")
    assert asks_about_past("شو كان في منهج الفصل الماضي عن الامتحانات؟")
    assert asks_about_past("ما هي المواد السابقة التي درستها؟")
    assert not asks_about_past("What does the syllabus say about makeup exams?")


def test_a_named_past_course_is_searched_and_the_answer_is_labelled_past() -> None:
    academic, documents = _world()
    assistant, gateway, rag = _assistant(academic, documents)

    result = asyncio.run(assistant.ask("What does the SE440 syllabus say about makeup exams?"))

    assert [query.scope for query in rag.queries] == [CourseScope(PAST_SE440.id)]
    assert result.status is AcademicAskStatus.ANSWERED
    assert (result.answer_text or "").startswith("From a past course (archived 2 Oct 2026):")
    assert result.rag_provenance is not None
    assert result.rag_provenance["from_past_course"] is True
    assert "past course" not in str(result.rag_provenance["rendered_text"])
    assert gateway.requests == []


def test_an_unscoped_question_searches_current_courses_only() -> None:
    academic, documents = _world()
    assistant, _gateway, rag = _assistant(academic, documents)

    result = asyncio.run(assistant.ask("What does the syllabus say about the exam policy?"))

    assert [query.scope for query in rag.queries] == [GlobalScope()]
    assert not (result.answer_text or "").startswith("From a past course")
    assert result.rag_provenance is not None
    assert "from_past_course" not in result.rag_provenance


def test_without_current_documents_an_unscoped_question_names_the_past_courses() -> None:
    academic, documents = _world(current_documents=False)
    assistant, _gateway, rag = _assistant(academic, documents)

    result = asyncio.run(assistant.ask("What does the syllabus say about the exam policy?"))

    assert rag.queries == []
    assert "current courses have no indexed documents" in (result.answer_text or "")
    assert "SE440-PROJECT MANAGEMENT" in (result.answer_text or "")


def test_past_wording_without_a_course_searches_past_course_documents() -> None:
    academic, documents = _world()
    assistant, _gateway, rag = _assistant(academic, documents)

    result = asyncio.run(
        assistant.ask("What did the syllabus of my previous courses say about makeup exams?")
    )

    assert [query.scope for query in rag.queries] == [HistoricalScope((202,))]
    assert (result.answer_text or "").startswith("From a past course (archived 2 Oct 2026):")


def test_a_retaken_course_resolves_to_the_current_one_unless_past_is_asked() -> None:
    academic, documents = _world()
    past_databases = academic.courses[1].model_copy(
        update={"id": 9, "status": "past", "archived_at": ARCHIVED}
    )
    academic.courses = [*academic.courses, past_databases]
    assistant, _gateway, rag = _assistant(academic, documents)

    asyncio.run(assistant.ask("What do the Databases notes say about indexes?"))
    asyncio.run(assistant.ask("What did the Databases notes say last semester about indexes?"))

    assert [query.scope for query in rag.queries] == [CourseScope(8), CourseScope(9)]


def test_my_courses_lists_current_courses_and_past_wording_lists_past_ones() -> None:
    academic, documents = _world()
    assistant, _gateway, _rag = _assistant(academic, documents)

    current = asyncio.run(assistant.ask("What courses am I registered in?"))
    past = asyncio.run(assistant.ask("What courses did I take last semester?"))

    assert "SE440" not in (current.answer_text or "")
    assert "Deep Learning" in (current.answer_text or "")
    assert "SE440-PROJECT MANAGEMENT" in (past.answer_text or "")
    assert "past course" in (past.answer_text or "")
    assert "Deep Learning" not in (past.answer_text or "")
    assert "Past courses:" in (past.answer_text or "")
    assert "Active courses" not in (past.answer_text or "")
