"""Phase I1: routed requests reach existing read capabilities without the model."""

from __future__ import annotations

import asyncio
from typing import Any

from test_agent_academic_orchestration import (
    NOW,
    FakeAcademicReader,
    FakeDocumentReader,
    FakeGateway,
    FakeRagService,
    _refuse,
)
from test_agent_moodle_changes import FakeMoodleChangeReader, change_set

from aaos.agent.academic_orchestrator import (
    AcademicAskStatus,
    ReadOnlyAcademicAssistant,
)
from aaos.rag.contracts import CourseScope, GlobalScope


def _assistant(
    gateway: FakeGateway,
    *,
    intent_routing: bool = True,
    change_reader: FakeMoodleChangeReader | None = None,
    **readers: Any,
) -> tuple[ReadOnlyAcademicAssistant, FakeAcademicReader, FakeRagService]:
    academic = readers.get("academic") or FakeAcademicReader()
    rag = readers.get("rag") or FakeRagService()
    assistant = ReadOnlyAcademicAssistant(
        gateway,
        academic,
        readers.get("documents") or FakeDocumentReader(),
        rag,
        clock=lambda: NOW,
        moodle_change_reader=change_reader,
        intent_routing=intent_routing,
    )
    return assistant, academic, rag


def test_arabic_course_question_is_answered_without_the_model() -> None:
    gateway = FakeGateway()
    assistant, academic, _rag = _assistant(gateway)

    result = asyncio.run(assistant.ask("ما هي المواد المسجلة لي؟"))

    assert result.status is AcademicAskStatus.ANSWERED
    assert "Deep Learning" in (result.answer_text or "")
    assert ("list_courses", 51) in academic.calls
    assert gateway.requests == []


def test_without_routing_the_same_arabic_question_depends_on_the_model() -> None:
    gateway = FakeGateway()
    assistant, _academic, _rag = _assistant(gateway, intent_routing=False)

    result = asyncio.run(assistant.ask("ما هي المواد المسجلة لي؟"))

    assert result.status is not AcademicAskStatus.ANSWERED
    assert len(gateway.requests) == 1


def test_arabic_document_question_uses_rag_with_the_trusted_course_scope() -> None:
    gateway = FakeGateway()
    assistant, _academic, rag = _assistant(gateway)

    asyncio.run(assistant.ask("ماذا تقول ملاحظات مادة CS401 عن الشبكات العصبية؟"))

    assert [query.scope for query in rag.queries] == [CourseScope(7)]
    assert rag.queries[0].question == "ماذا تقول ملاحظات مادة CS401 عن الشبكات العصبية؟"
    assert gateway.requests == []


def test_document_question_without_a_course_searches_globally() -> None:
    gateway = FakeGateway()
    assistant, _academic, rag = _assistant(gateway)

    asyncio.run(assistant.ask("What does the syllabus say about the makeup exam policy?"))

    assert [query.scope for query in rag.queries] == [GlobalScope()]
    assert gateway.requests == []


def test_assignments_due_this_week_use_the_trusted_seven_day_window() -> None:
    gateway = FakeGateway()
    assistant, _academic, _rag = _assistant(gateway)

    result = asyncio.run(assistant.ask("What assignments are due this week?"))

    assert result.status is AcademicAskStatus.ANSWERED
    assert "Neural Networks Report" in (result.answer_text or "")
    assert "SQL Project" not in (result.answer_text or "")
    assert gateway.requests == []


def test_arabic_change_question_reaches_the_read_only_change_capability() -> None:
    gateway = FakeGateway()
    reader = FakeMoodleChangeReader(change_set())
    assistant, _academic, _rag = _assistant(gateway, change_reader=reader)

    result = asyncio.run(assistant.ask("ما الذي تغير في المودل منذ آخر مزامنة؟"))

    assert result.status is AcademicAskStatus.ANSWERED
    assert len(reader.calls) == 1
    assert gateway.requests == []


def test_change_question_without_a_change_reader_falls_back_to_the_model() -> None:
    gateway = FakeGateway(_refuse("UNSUPPORTED_CAPABILITY"))
    assistant, _academic, _rag = _assistant(gateway)

    asyncio.run(assistant.ask("ما الذي تغير في المودل؟"))

    assert len(gateway.requests) == 1


def test_unrouted_and_write_requests_still_use_the_model_loop() -> None:
    for question in ("Hello, how are you?", "أنشئ مهمة دراسية لمراجعة مادة CS401"):
        gateway = FakeGateway(_refuse("UNSUPPORTED_CAPABILITY"))
        assistant, _academic, rag = _assistant(gateway)

        asyncio.run(assistant.ask(question))

        assert len(gateway.requests) == 1
        assert rag.queries == []


def test_question_naming_two_courses_is_not_routed_to_a_guessed_scope() -> None:
    gateway = FakeGateway(_refuse("UNSUPPORTED_CAPABILITY"))
    assistant, _academic, rag = _assistant(gateway)

    asyncio.run(assistant.ask("What do the Deep Learning and Databases notes say about indexes?"))

    assert len(gateway.requests) == 1
    assert rag.queries == []

