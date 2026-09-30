"""Deterministic course-name conventions shared by agent course resolvers."""

from __future__ import annotations

import re
import unicodedata

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
