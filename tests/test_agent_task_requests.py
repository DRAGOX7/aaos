"""An explicit "add a study task" sentence is understood in code, not by the model.

Regression: "add a study task to AI380-..." (a course but no task) failed with a
generic error, and "... for STAI 401" (a file-name prefix, not a course) failed
with "couldn't find that course". A small local model was left to turn loose
wording into a valid ``study_task.create`` call.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from test_agent_study_task_actions import (
    FakeAcademicReader,
    FakeDocumentReader,
    FakeGateway,
    FakeRagService,
    task_service,  # noqa: F401  (pytest fixture)
)

from aaos.agent.academic_orchestrator import AcademicAskStatus, ReadOnlyAcademicAssistant
from aaos.agent.intent_router import fold_with_offsets, normalize_arabic
from aaos.agent.task_requests import TaskRequest, parse_task_request
from aaos.study_tasks.contracts import StudyTaskPriority
from aaos.study_tasks.service import StudyTaskService
from aaos.web.contracts import CourseSummary

# The owner's real Moodle titles, each course with its section entries.
_TITLES = (
    ("AI380-OPTIMIZATION ALGORITHMS (1)", "current"),
    ("AI380-OPTIMIZATION ALGORITHMS (All Sections )", "current"),
    ("AI445-NATURAL LANGUAGE PROCESSING (1)", "current"),
    ("AI445-NATURAL LANGUAGE PROCESSING (All Sections )", "current"),
    ("RB201-INTRODUCTION TO ROBOTICS (All Sections )", "current"),
    ("AI495C-SPECIAL TOPICS IN ARTIFICIAL INTELLIGENCE (3) (All Sections )", "past"),
)
COURSES = tuple(
    CourseSummary(id=index + 1, title=title, code=None, description=None, updated_at=None, status=s)
    for index, (title, s) in enumerate(_TITLES)
)


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        # The request that failed with a generic error: a course, but no task.
        (
            "ADD A STUDY TASK TO AI380-OPTIMIZATION ALGORITHMS (All Sections )",
            TaskRequest(title=None, course_name="ai380"),
        ),
        ("I want you to create a study task for AI 380", TaskRequest(course_name="ai380")),
        # The request that failed with "couldn't find that course": STAI 401 is a
        # file-name prefix, so it is flagged instead of guessed.
        (
            "create a study task to finish the KNIME notes for STAI 401",
            TaskRequest(title="finish the KNIME notes for STAI 401", unknown_course="STAI 401"),
        ),
        (
            "add a study task: review chapter 3 for Natural Language Processing",
            TaskRequest(title="review chapter 3", course_name="ai445"),
        ),
        (
            "make a task for AI445 to read the BERT paper, high priority",
            TaskRequest(title="read the BERT paper", course_name="ai445", priority="HIGH"),
        ),
        (
            "make me a todo: submit the robotics report for RB201",
            TaskRequest(title="submit the robotics report", course_name="rb201"),
        ),
        ('add "finish lab 2" to my tasks', TaskRequest(title="finish lab 2")),
        (
            "can you create a study task: practice SQL joins",
            TaskRequest(title="practice SQL joins"),
        ),
        # An archived course is not linked, and its code is not called unknown.
        (
            "add a study task to AI495 review KNIME notes",
            TaskRequest(title="AI495 review KNIME notes"),
        ),
        (
            "ضيفلي مهمة دراسية: مراجعة الفصل الثالث لمادة AI445",
            TaskRequest(title="مراجعة الفصل الثالث", course_name="ai445"),
        ),
    ),
)
def test_a_task_sentence_is_parsed_into_title_and_course(
    question: str, expected: TaskRequest
) -> None:
    assert parse_task_request(question, COURSES) == expected


@pytest.mark.parametrize(
    "question",
    (
        "what is a task",
        "how do I add a study task?",
        "tell me about the AI380 task list",
        # Pointing at something else: the model loop and its trusted context decide.
        "add a study task for that assignment",
        "create a task for the second one",
        "add a study task and put it on my calendar",
        "make a task for the Neural Networks assignment",
    ),
)
def test_other_sentences_are_left_to_the_existing_flow(question: str) -> None:
    assert parse_task_request(question, COURSES) is None


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        # A priority written before the noun is understood by the rules.
        (
            "make a high-priority study task to review chapter 2 for AI445",
            TaskRequest(title="review chapter 2", course_name="ai445", priority="HIGH"),
        ),
        (
            "add an urgent task: read the BERT paper for natural language processing",
            TaskRequest(title="read the BERT paper", course_name="ai445", priority="HIGH"),
        ),
        (
            "create a low priority study task: tidy my notes",
            TaskRequest(title="tidy my notes", priority="LOW"),
        ),
        (
            "add a normal-priority task to practise sorting",
            TaskRequest(title="practise sorting", priority="NORMAL"),
        ),
        # A word order or plural the student actually types still links the course;
        # the owner named only a course, so the task itself is asked for.
        (
            "MAKE A HIGH-PRIORITY STUDY TASK FOR ALGORITHM OPTIMIZATION",
            TaskRequest(course_name="ai380", priority="HIGH"),
        ),
        (
            "make a study task for optimization algorithm: read chapter 4",
            TaskRequest(title="read chapter 4", course_name="ai380"),
        ),
        (
            "make a study task to finish the lab for robotics introduction",
            TaskRequest(title="finish the lab", course_name="rb201"),
        ),
    ),
)
def test_a_priority_before_the_noun_and_a_looser_course_name_are_understood(
    question: str, expected: TaskRequest
) -> None:
    assert parse_task_request(question, COURSES) == expected


def test_the_looser_course_match_is_for_linking_tasks_not_for_scoping_questions() -> None:
    from aaos.agent.intent_router import find_course_mention

    current = tuple(course for course in COURSES if course.status != "past")
    question = "what is the optimization algorithm for training a network"

    assert find_course_mention(question, current).course_name is None  # a question stays unscoped
    assert find_course_mention(question, current, loose=True).course_name == "ai380"


@pytest.mark.parametrize(
    "phrase", ("optimization only", "algorithms only", "language processing", "introduction")
)
def test_a_single_word_or_a_partial_name_never_links_a_course(phrase: str) -> None:
    request = parse_task_request(f"add a study task: read about {phrase}", COURSES)

    assert request is not None and request.course_name is None


def test_a_bare_priority_task_request_asks_what_the_task_is() -> None:
    assert parse_task_request("make a high-priority study task", COURSES) == TaskRequest(
        priority="HIGH"
    )


def test_folding_keeps_offsets_into_the_original_text() -> None:
    text = "  Add   A Study TASK:  مراجعة الفصل  "
    folded, origins = fold_with_offsets(text)

    assert folded == normalize_arabic(text)
    assert len(folded) == len(origins)
    assert text[origins[folded.index("study")] : origins[folded.index("study")] + 5] == "Study"


# --- through the assistant -------------------------------------------------------------------


def _assistant(service: StudyTaskService, gateway: FakeGateway) -> ReadOnlyAcademicAssistant:
    return ReadOnlyAcademicAssistant(
        gateway,
        FakeAcademicReader(),
        FakeDocumentReader(),
        FakeRagService(),
        study_task_service=service,
        intent_routing=True,
    )


def _ask(assistant: ReadOnlyAcademicAssistant, question: str, request: int = 1):  # type: ignore[no-untyped-def]
    return asyncio.run(
        assistant.ask(question, request_id=UUID(f"20000000-0000-4000-8000-{request:012d}"))
    )


def test_a_task_with_a_course_but_no_title_asks_what_the_task_is(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    gateway = FakeGateway()
    result = _ask(_assistant(task_service, gateway), "ADD A STUDY TASK TO CS301")

    assert result.status is AcademicAskStatus.ANSWERED
    assert "What should the task be?" in (result.answer_text or "")
    assert "the course Databases," in (result.answer_text or "")
    assert gateway.requests == []
    assert task_service.list() == ()


def test_a_course_code_that_matches_no_course_is_not_guessed(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    gateway = FakeGateway()
    result = _ask(
        _assistant(task_service, gateway),
        "create a study task to finish the KNIME notes for STAI 401",
    )

    assert result.status is AcademicAskStatus.ANSWERED
    answer = result.answer_text or ""
    assert "STAI 401" in answer and "Your current courses" in answer
    assert "Deep Learning" in answer and "Databases" in answer
    assert gateway.requests == []
    assert task_service.list() == ()


def test_an_explicit_task_is_saved_without_asking_the_model(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    """The model is not needed for a complete sentence: no scripted decisions at all."""
    gateway = FakeGateway()
    result = _ask(
        _assistant(task_service, gateway),
        "make me a todo: practice joins for Databases, high priority",
    )

    assert result.status is AcademicAskStatus.ANSWERED
    (task,) = task_service.list()
    assert task.title == "practice joins"
    assert task.course_id == 8
    assert task.priority is StudyTaskPriority.HIGH
    assert gateway.requests == []
    assert len(result.sources) == 1


def test_a_task_without_a_course_is_saved_unlinked(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    gateway = FakeGateway()
    result = _ask(_assistant(task_service, gateway), 'add "finish lab 2" to my tasks')

    assert result.status is AcademicAskStatus.ANSWERED
    (task,) = task_service.list()
    assert (task.title, task.course_id) == ("finish lab 2", None)
    assert gateway.requests == []


def test_an_arabic_task_is_saved_with_its_arabic_title(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    gateway = FakeGateway()
    result = _ask(
        _assistant(task_service, gateway),
        "ضيفلي مهمة دراسية: مراجعة الفصل الثالث لمادة Databases",
    )

    assert result.status is AcademicAskStatus.ANSWERED
    (task,) = task_service.list()
    assert task.title == "مراجعة الفصل الثالث"
    assert task.course_id == 8


def test_replaying_the_same_request_saves_one_task(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    assistant = _assistant(task_service, FakeGateway())

    first = _ask(assistant, "add a study task: read chapter 4", request=7)
    replay = _ask(assistant, "add a study task: read chapter 4", request=7)

    assert first.status is replay.status is AcademicAskStatus.ANSWERED
    assert len(task_service.list()) == 1


def test_a_pronoun_task_request_is_left_to_the_existing_flow(
    task_service: StudyTaskService,  # noqa: F811
) -> None:
    """Not parsed in code: with no earlier answer to point at, the existing guard refuses."""
    gateway = FakeGateway()
    result = _ask(_assistant(task_service, gateway), "add a study task for that assignment")

    assert result.status is AcademicAskStatus.REFUSED
    assert "previous reference" in (result.answer_text or "")
    assert task_service.list() == ()
