"""Trusted recognition of bounded Moodle-change asks and time windows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from aaos.moodle.changes import ComparisonUnavailableReason

_CHANGE_QUERY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhat\s+(?:has\s+)?changed\b",
        r"\b(?:anything|any|what)\b.{0,64}\b(?:changed|updated)\b",
        r"\b(?:what|any|anything|show|list)\b.{0,48}\bchanges?\b",
        r"\bwhich\s+courses?\s+(?:have\s+)?changed\b",
        r"\b(?:did|has|have)\b.{0,64}\b(?:deadline|due\s+date)\b.{0,48}\b(?:change|changed|move|moved|update|updated)\b",
        r"\b(?:is|are|was|were|did|does|do|has|have)\b.{0,64}"
        r"\b(?:change|changed|move|moved|update|updated)\b.{0,48}"
        r"\b(?:deadline|due\s+date)\b",
        r"\b(?:new|added|removed|uploaded|upload)\b.{0,48}\b(?:assignments?|documents?|files?|resources?|courses?)\b",
        r"\b(?:assignments?|documents?|files?|resources?|courses?)\b.{0,48}\b(?:new|added|removed|uploaded|changed|updated)\b",
        r"\b(?:what|anything|any|which)\b.{0,64}\b(?:new|added|removed|uploaded|changed)\b",
    )
)
_CHANGE_DEADLINE_ACTION_PATTERN = re.compile(
    r"\b(?:change|changed|move|moved|update|updated)\b.{0,48}\b(?:deadline|due\s+date)\b",
    re.IGNORECASE,
)
_SINCE_ANY = re.compile(r"\bsince\b", re.IGNORECASE)
_SINCE_YESTERDAY = re.compile(r"\bsince\s+(?:the\s+)?yesterday\b", re.IGNORECASE)
_SINCE_LAST_SYNC = re.compile(
    r"\bsince\s+(?:(?:the|my)\s+)?(?:(?:last|previous)\s+)?"
    r"(?:last\s+|previous\s+)?(?:successful\s+)?sync(?:hronization)?\b",
    re.IGNORECASE,
)
_SINCE_ISO_DATE = re.compile(r"\bsince\s+(\d{4}-\d{2}-\d{2})(?![\dT])", re.IGNORECASE)
_SINCE_ISO_DATETIME = re.compile(
    r"\bsince\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2}))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ChangeTimeWindow:
    boundary: datetime | None
    unavailable_reason: ComparisonUnavailableReason | None = None

    def __post_init__(self) -> None:
        if self.unavailable_reason is not None:
            if not isinstance(self.unavailable_reason, ComparisonUnavailableReason):
                raise TypeError("unavailable_reason must use its closed enum")
            if self.boundary is not None:
                raise ValueError("unavailable time windows cannot carry a boundary")
        elif self.boundary is not None and (
            self.boundary.tzinfo is None or self.boundary.utcoffset() is None
        ):
            raise ValueError("time window boundary must be timezone-aware")


def is_moodle_change_query(text: str) -> bool:
    """Recognize supported change queries, excluding action-shaped deadline wording."""
    if not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in _CHANGE_QUERY_PATTERNS)


def is_moodle_change_request(text: str) -> bool:
    """Recognize supported change intent from original user text only."""
    return is_moodle_change_query(text) or (
        isinstance(text, str) and _CHANGE_DEADLINE_ACTION_PATTERN.search(text) is not None
    )


def resolve_change_time_window(text: str, *, now: datetime) -> ChangeTimeWindow:
    """Parse only stable named windows and explicit ISO timestamps; never model arguments."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    now = _utc(now)
    if not _SINCE_ANY.search(text):
        return ChangeTimeWindow(None)
    if _SINCE_YESTERDAY.search(text):
        return ChangeTimeWindow(now - timedelta(hours=24))
    if _SINCE_LAST_SYNC.search(text):
        return ChangeTimeWindow(None)
    datetime_match = _SINCE_ISO_DATETIME.search(text)
    if datetime_match is not None:
        try:
            parsed = datetime.fromisoformat(datetime_match.group(1).replace("Z", "+00:00"))
        except ValueError:
            return _unsupported()
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return _unsupported()
        return ChangeTimeWindow(parsed.astimezone(timezone.utc))
    date_match = _SINCE_ISO_DATE.search(text)
    if date_match is not None:
        try:
            day = date.fromisoformat(date_match.group(1))
        except ValueError:
            return _unsupported()
        return ChangeTimeWindow(datetime.combine(day, time.min, tzinfo=timezone.utc))
    return _unsupported()


def _unsupported() -> ChangeTimeWindow:
    return ChangeTimeWindow(
        None,
        ComparisonUnavailableReason.UNSUPPORTED_TIME_WINDOW,
    )


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return value.astimezone(timezone.utc)


__all__ = [
    "ChangeTimeWindow",
    "is_moodle_change_query",
    "is_moodle_change_request",
    "resolve_change_time_window",
]
