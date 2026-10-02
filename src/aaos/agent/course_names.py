"""Deterministic course-name conventions shared by agent course resolvers."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

# Moodle course titles carry the course code before the first hyphen, for
# example "SE440-PROJECT MANAGEMENT (All Sections )". The pattern applies to
# normalized titles.
_COURSE_CODE_PREFIX = re.compile(r"([a-z]{2,}[0-9]{2,}[a-z0-9.]*)\s*-")


def normalize_course_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def course_code_token(title: str) -> str | None:
    """Return the normalized course code leading a Moodle title, if any."""
    match = _COURSE_CODE_PREFIX.match(normalize_course_text(title))
    return match.group(1) if match else None


# Readable course names for answer text. Moodle titles look like
# "AI380-OPTIMIZATION ALGORITHMS (All Sections )": a code, a dash, an all-caps
# name and a section suffix. The web client applies the same rules
# (frontend/lib/courses/display.ts).
_MOODLE_TITLE = re.compile(r"^([A-Z]{2,4}\d{3}[A-Z]?(?:\.[A-Z]+)?)\s*-\s*(.+)$")
_ALL_SECTIONS = re.compile(r"\s*\(\s*All Sections\s*\)\s*$", re.IGNORECASE)
_SECTION_NUMBER = re.compile(r"\s*\(\d+\)\s*$")
_PAST_SUFFIX = " (past course)"
_SMALL_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
)


def _title_case(text: str) -> str:
    words = []
    for index, word in enumerate(text.lower().split(" ")):
        if index > 0 and word in _SMALL_WORDS:
            words.append(word)
            continue
        opening = "(" if word.startswith("(") else ""
        rest = word[len(opening) :]
        words.append(opening + rest[:1].upper() + rest[1:])
    return " ".join(words)


def course_display_title(title: str) -> str:
    """'AI380-OPTIMIZATION ALGORITHMS (All Sections )' -> 'AI380 · Optimization Algorithms'.

    Titles that are not Moodle code titles are returned unchanged. A trailing
    " (past course)" label is kept.
    """
    text = title.strip()
    past = text.endswith(_PAST_SUFFIX)
    if past:
        text = text[: -len(_PAST_SUFFIX)].strip()
    match = _MOODLE_TITLE.match(text)
    if match is None:
        return title.strip()
    name = match.group(2)
    if _ALL_SECTIONS.search(name):
        name = _ALL_SECTIONS.sub("", name)
    else:
        name = _SECTION_NUMBER.sub("", name)  # the section entry's own number
    name = " ".join(re.sub(r"(\S)\(", r"\1 (", name).split())
    if name == name.upper():
        name = _title_case(name)
    return f"{match.group(1)} · {name}" + (_PAST_SUFFIX if past else "")


_ARABIC_MONTHS = (
    "كانون الثاني",
    "شباط",
    "آذار",
    "نيسان",
    "أيار",
    "حزيران",
    "تموز",
    "آب",
    "أيلول",
    "تشرين الأول",
    "تشرين الثاني",
    "كانون الأول",
)


def arabic_date(day: date) -> str:
    """'2 تشرين الأول 2026': a day, a Levantine month name and a year.

    Spelled out so the date reads correctly inside right-to-left text, where
    an ISO date's hyphenated digits are reordered by the bidi algorithm.
    """
    return f"{day.day} {_ARABIC_MONTHS[day.month - 1]} {day.year}"
