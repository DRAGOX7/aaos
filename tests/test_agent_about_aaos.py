"""Questions about AAOS itself get a fixed answer and never search course files."""

from __future__ import annotations

import pytest
from test_agent_archived_courses import _conversation, _world

from aaos.agent.academic_orchestrator import AcademicAskStatus
from aaos.agent.intent_router import RoutedIntent, asks_about_assistant, route_intent
from aaos.rag.contracts import HistoricalScope

ABOUT_QUESTIONS = (
    "what is AAOS",
    "What is AAOS?",
    "WHAT IS YOUR GOAL",
    "what's your purpose?",
    "who are you",
    "what can you do",
    "tell me about yourself",
    "introduce yourself",
    "مين انت؟",
    "شو هدفك",
    "ما هو AAOS؟",
    "شو بتقدر تعمل",
)


@pytest.mark.parametrize("question", ABOUT_QUESTIONS)
def test_questions_about_the_assistant_are_recognized(question: str) -> None:
    assert asks_about_assistant(question)
    assert route_intent(question) is RoutedIntent.UNROUTED


@pytest.mark.parametrize(
    "question",
    (
        "what is knime",
        "what is the goal of lab 2?",
        "what is the purpose of cross validation",
        "who is the instructor of AI445",
        "ما هو KNIME؟",
        "شو هدف المشروع",
    ),
)
def test_course_questions_are_not_mistaken_for_questions_about_aaos(question: str) -> None:
    assert not asks_about_assistant(question)


def test_what_is_aaos_answers_without_searching_files_and_keeps_the_past_focus() -> None:
    """Regression: after a past-course answer, "what is AAOS" searched the KNIME notes."""
    ask, rag = _conversation(*_world())

    ask("what is knime from my previous semester?")
    about = ask("what is AAOS")
    goal = ask("WHAT IS YOUR GOAL")
    ask("tell me what knime is")

    for result in (about, goal):
        assert result.status is AcademicAskStatus.ANSWERED
        assert (result.answer_text or "").startswith("I'm AAOS, your academic assistant")
        assert result.sources == ()
    # Only the two KNIME questions searched, both still in the past course.
    assert [query.scope for query in rag.queries] == [
        HistoricalScope((202,)),
        HistoricalScope((202,)),
    ]


def test_an_arabic_question_about_aaos_is_answered_in_arabic() -> None:
    ask, rag = _conversation(*_world())

    result = ask("شو هدفك؟")

    assert (result.answer_text or "").startswith("أنا AAOS")
    assert rag.queries == []
