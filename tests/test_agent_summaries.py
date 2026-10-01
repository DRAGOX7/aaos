"""Phase I3: routed summary questions reach the verified summarizer, not the model."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from test_agent_academic_orchestration import (
    NOW,
    FakeAcademicReader,
    FakeDocumentReader,
    FakeGateway,
    FakeRagService,
    _rag_grounded_answer,
    _refuse,
)

from aaos.agent.academic_orchestrator import AcademicAskStatus, ReadOnlyAcademicAssistant
from aaos.rag.contracts import RagErrorCode, RagOperationalError
from aaos.rag.summaries import SummaryOutcome, SummaryResult


class FakeSummarizer:
    def __init__(self, outcome: SummaryOutcome = SummaryOutcome.READY) -> None:
        self.outcome = outcome
        self.calls: list[tuple[tuple[int, ...], str, float | None]] = []
        self.error: Exception | None = None

    async def summarize_course(
        self,
        document_version_ids: Sequence[int],
        language: str,
        *,
        deadline: float | None = None,
    ) -> SummaryResult:
        versions = tuple(document_version_ids)
        self.calls.append((versions, language, deadline))
        if self.error is not None:
            raise self.error
        if self.outcome is SummaryOutcome.READY:
            return SummaryResult(SummaryOutcome.READY, _rag_grounded_answer(), versions)
        if self.outcome is SummaryOutcome.NOT_PREPARED:
            return SummaryResult(
                SummaryOutcome.NOT_PREPARED,
                None,
                versions,
                missing_document_version_ids=versions,
            )
        return SummaryResult(SummaryOutcome.NO_VERIFIED_POINTS, None, versions, dropped_points=3)


def _documents(*extra: tuple[int, int, str, int]) -> FakeDocumentReader:
    reader = FakeDocumentReader()
    template = reader.documents[0]
    reader.documents = [
        template,
        *(
            template.model_copy(
                update={
                    "id": document_id,
                    "course_id": course_id,
                    "title": title,
                    "document_version_id": version,
                }
            )
            for document_id, course_id, title, version in extra
        ),
    ]
    return reader


def _assistant(
    summarizer: FakeSummarizer | None,
    *,
    gateway: FakeGateway | None = None,
    documents: FakeDocumentReader | None = None,
) -> tuple[ReadOnlyAcademicAssistant, FakeGateway, FakeRagService]:
    resolved_gateway = gateway or FakeGateway()
    rag = FakeRagService()
    assistant = ReadOnlyAcademicAssistant(
        resolved_gateway,
        FakeAcademicReader(),
        documents or FakeDocumentReader(),
        rag,
        clock=lambda: NOW,
        intent_routing=True,
        summarizer=summarizer,
    )
    return assistant, resolved_gateway, rag


def test_document_summary_returns_the_verified_cited_summary_without_the_model() -> None:
    summarizer = FakeSummarizer()
    assistant, gateway, rag = _assistant(summarizer)

    result = asyncio.run(assistant.ask("Summarize the Lecture 1 slides for CS401"))

    assert result.status is AcademicAskStatus.ANSWERED
    assert "compare two neural network architectures" in (result.answer_text or "")
    assert result.rag_provenance is not None
    assert [(call[0], call[1]) for call in summarizer.calls] == [((101,), "en")]
    assert summarizer.calls[0][2] is not None
    assert gateway.requests == []
    assert rag.queries == []


def test_arabic_summary_request_asks_for_an_arabic_summary() -> None:
    summarizer = FakeSummarizer()
    assistant, gateway, _rag = _assistant(summarizer)

    asyncio.run(assistant.ask("لخص محاضرة مادة CS401"))

    assert [(call[0], call[1]) for call in summarizer.calls] == [((101,), "ar")]
    assert gateway.requests == []


def test_document_summary_picks_the_document_named_in_the_question() -> None:
    summarizer = FakeSummarizer()
    documents = _documents((31, 7, "Course Syllabus", 102))
    assistant, _gateway, _rag = _assistant(summarizer, documents=documents)

    asyncio.run(assistant.ask("Summarize the Deep Learning syllabus"))

    assert [call[0] for call in summarizer.calls] == [(102,)]


def test_document_summary_that_names_no_single_document_is_ambiguous() -> None:
    summarizer = FakeSummarizer()
    documents = _documents((31, 7, "Course Syllabus", 102))
    assistant, _gateway, _rag = _assistant(summarizer, documents=documents)

    result = asyncio.run(assistant.ask("Summarize the Deep Learning notes"))

    assert result.failure_code is not None
    assert result.failure_code.value == "AMBIGUOUS_DOCUMENT"
    assert summarizer.calls == []


def test_course_summary_combines_every_indexed_document_of_the_course() -> None:
    summarizer = FakeSummarizer()
    documents = _documents((31, 7, "Course Syllabus", 102), (32, 8, "SQL Notes", 103))
    assistant, _gateway, _rag = _assistant(summarizer, documents=documents)

    result = asyncio.run(assistant.ask("Give me an overview of the Deep Learning course"))

    assert result.status is AcademicAskStatus.ANSWERED
    assert [call[0] for call in summarizer.calls] == [(101, 102)]


def test_summary_that_is_not_prepared_is_reported_honestly() -> None:
    assistant, _gateway, _rag = _assistant(FakeSummarizer(SummaryOutcome.NOT_PREPARED))

    result = asyncio.run(assistant.ask("Summarize the Lecture 1 slides for CS401"))

    assert result.status is not AcademicAskStatus.ANSWERED
    assert result.failure_code is not None
    assert result.failure_code.value == "SUMMARY_NOT_PREPARED"


def test_summary_without_verified_points_is_reported_honestly() -> None:
    assistant, _gateway, _rag = _assistant(FakeSummarizer(SummaryOutcome.NO_VERIFIED_POINTS))

    result = asyncio.run(assistant.ask("Summarize the Lecture 1 slides for CS401"))

    assert result.failure_code is not None
    assert result.failure_code.value == "SUMMARY_UNVERIFIED"


def test_summary_model_outage_maps_to_the_existing_model_failure() -> None:
    summarizer = FakeSummarizer()
    summarizer.error = RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE)
    assistant, _gateway, _rag = _assistant(summarizer)

    result = asyncio.run(assistant.ask("Summarize the Lecture 1 slides for CS401"))

    assert result.failure_code is not None
    assert result.failure_code.value == "MODEL_UNAVAILABLE"


def test_course_without_indexed_documents_has_nothing_to_summarize() -> None:
    summarizer = FakeSummarizer()
    assistant, _gateway, _rag = _assistant(summarizer)

    result = asyncio.run(assistant.ask("Give me an overview of the Databases course"))

    assert result.failure_code is not None
    assert result.failure_code.value == "NO_INDEXED_DOCUMENTS"
    assert summarizer.calls == []


def test_without_a_summarizer_summary_questions_keep_using_the_model_loop() -> None:
    gateway = FakeGateway(_refuse("UNSUPPORTED_CAPABILITY"))
    assistant, _gateway, _rag = _assistant(None, gateway=gateway)

    asyncio.run(assistant.ask("Summarize the Lecture 1 slides for CS401"))

    assert len(gateway.requests) == 1
