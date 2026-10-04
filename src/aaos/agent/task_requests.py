"""Understand "add a study task ..." requests in code, not by asking the model.

A small local model is unreliable at turning loose wording into a valid
``study_task.create`` call, so the owner's own explicit sentence is parsed here:
the leader ("add a study task", "make a task", Arabic equivalents) is anchored at
the start of the message, the title is what is left once the course the owner
names is cut out, and the course comes only from trusted course records.

This grants no authority. A parsed request becomes the same ``study_task.create``
call a model could propose, validated by the same registry, permission policy
and argument checks; anything unclear (a pronoun, an assignment, a calendar
word) returns ``None`` and stays with the existing model loop.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from aaos.web.contracts import CourseSummary

from .intent_router import course_alias_patterns, find_course_mention, fold_with_offsets

MAX_TASK_TITLE_CHARACTERS = 500

_LEADER = re.compile(
    r"^(?:please |pls |kindly )?"
    r"(?:(?:can|could|would) you |i want you to |i want to |i need to |i would like to |"
    r"i'd like to |let's |lets |help me |)"
    r"(?:please )?(?:add|create|make|set up|setup|start|new)(?: me)?(?: a| an| one| another)?"
    r"(?: new)?(?: study)? (?:task|todo|to-do)(?: for me)?(?: please)?(?![\w])"
    r"|"
    r"^(?:بدي |اريد |ممكن |لو سمحت |)"
    r"(?:تعمل|تعملي|تضيف|تضيفلي|تنشي|انشي|اعمل|اعملي|ضيف|ضيفلي|اضف|اضافه)(?: لي)? "
    r"(?:مهمه|تاسك)(?: دراسيه| جديده)?(?![\w])"
)
_ADD_TO_LIST = re.compile(
    r"^(?:please )?(?:add|put) (?P<item>.+?) (?:to|on|in|into) my "
    r"(?:study )?(?:tasks?|to-?do(?: list)?|todos?)$"
)
# Wording that points at something else (a pronoun for an earlier answer, an
# assignment to link, a Calendar event): not a plain new task, leave it to the
# model loop and its trusted conversation context.
_DEFERS_TO_MODEL = re.compile(
    r"(?<!\w)(?:assignments?|homework|calendar|events?|reminders?|schedule|"
    r"that|this|it|them|these|those|ones?|"
    r"الواجب|التقويم|تذكير|هذا|هذه|هاد|هاي|ذلك)(?!\w)"
)
_PRIORITY = re.compile(
    r"(?<!\w)(?:(low|normal|high)[\s-]priority|priority\s*[:=]?\s*(low|normal|high))(?!\w)",
    re.IGNORECASE,
)
_COURSE_SECTION_LEFTOVER = re.compile(r"\(\s*(?:all sections|\d+)\s*\)", re.IGNORECASE)
_EMPTY_PARENTHESES = re.compile(r"\(\s*\)")
_CONNECTORS = (
    r"to|for|in|on|of|about|that|called|named|titled|is|from|at|with|and|"
    r"على|في|عن|حول|اسمها|بعنوان|لمادة|لمقرر|ل"
)
_PUNCTUATION = r"[\s:,;.\-–—]"
_LEADING = re.compile(rf"^(?:{_PUNCTUATION}+|(?<!\w)(?:{_CONNECTORS})(?!\w))+", re.IGNORECASE)
_TRAILING = re.compile(rf"(?:{_PUNCTUATION}+|(?<!\w)(?:{_CONNECTORS})(?!\w))+$", re.IGNORECASE)
# A course code written the way Moodle writes them: letters then three digits.
_CODE_LIKE = re.compile(r"(?<!\w)[A-Za-z]{2,4}[\s-]?\d{3}[A-Za-z]?(?!\w)")
_MAX_COURSE_CUTS = 8


@dataclass(frozen=True)
class TaskRequest:
    """What the owner's "add a study task" sentence says."""

    title: str | None = None
    # A resolver-ready name from trusted current-course records, or None.
    course_name: str | None = None
    # A course-code-shaped word that matches no course, current or past.
    unknown_course: str | None = None
    priority: str | None = None


def parse_task_request(question: str, courses: Sequence[CourseSummary]) -> TaskRequest | None:
    """Parse a plain, explicit request to add a study task; None if it is not one."""
    folded, origins = fold_with_offsets(question)
    leader = _LEADER.match(folded)
    if leader is not None:
        start = leader.end()
        stop = len(folded)
    else:
        listed = _ADD_TO_LIST.match(folded)
        if listed is None:
            return None
        start, stop = listed.span("item")
    if _DEFERS_TO_MODEL.search(folded[start:stop]) is not None:
        return None
    if start >= stop:
        return TaskRequest()
    rest = question[origins[start] : origins[stop - 1] + 1]

    current = tuple(course for course in courses if course.status != "past")
    mention = find_course_mention(rest, current)
    if mention.course_name is not None:
        folded_rest = fold_with_offsets(rest)[0]
        named = tuple(
            course
            for course in current
            if any(pattern.search(folded_rest) for pattern in course_alias_patterns(course))
        )
        rest = _without_course(rest, named)
    rest, priority = _without_priority(rest)
    title = _clean_title(rest)
    if title is not None and len(title) > MAX_TASK_TITLE_CHARACTERS:
        return None

    unknown = None
    if title is not None and mention.course_name is None:
        unknown = _unknown_course_code(title, courses)
    return TaskRequest(
        title=title,
        course_name=mention.course_name,
        unknown_course=unknown,
        priority=priority,
    )


def _without_course(text: str, courses: Sequence[CourseSummary]) -> str:
    """Cut every way the named courses are written out of ``text``."""
    patterns = [pattern for course in courses for pattern in course_alias_patterns(course)]
    for _ in range(_MAX_COURSE_CUTS):
        folded, origins = fold_with_offsets(text)
        match = next((m for m in (p.search(folded) for p in patterns) if m is not None), None)
        if match is None:
            break
        begin = origins[match.start()]
        end = origins[match.end() - 1] + 1
        text = f"{text[:begin]} {text[end:]}"
    return text


def _without_priority(text: str) -> tuple[str, str | None]:
    match = _PRIORITY.search(text)
    if match is None:
        return text, None
    level = (match.group(1) or match.group(2)).upper()
    return f"{text[: match.start()]} {text[match.end() :]}", level


def _clean_title(text: str) -> str | None:
    text = _COURSE_SECTION_LEFTOVER.sub(" ", text)
    text = _EMPTY_PARENTHESES.sub(" ", text)
    text = " ".join(text.split())
    text = _LEADING.sub("", text)
    text = _TRAILING.sub("", text)
    text = text.strip(" \"'“”«»")
    text = " ".join(text.split())
    if len(re.findall(r"\w", text)) < 2:
        return None
    return text


def _unknown_course_code(title: str, courses: Sequence[CourseSummary]) -> str | None:
    """A course-code-shaped word in the title that no course, current or past, owns."""
    for match in _CODE_LIKE.finditer(title):
        folded = fold_with_offsets(match.group(0))[0]
        if not any(
            pattern.search(folded)
            for course in courses
            for pattern in course_alias_patterns(course)
        ):
            return match.group(0)
    return None
