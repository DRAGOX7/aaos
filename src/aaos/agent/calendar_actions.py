"""Trusted planning for the conversational Google Calendar create proposal."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from aaos.integrations.google_calendar.contracts import CalendarCreateRequest
from aaos.study_tasks.contracts import StudyTask, StudyTaskStatus
from aaos.web.contracts import AssignmentSummary, TaskDetail

_CALENDAR_VERB = r"(?:put|add|create|schedule)"
_CALENDAR_DESTINATION = re.compile(
    rf"\b{_CALENDAR_VERB}\b.{{0,120}}\b(?:on|in|to)\s+(?:my\s+)?calendar\b|"
    rf"\bschedule\b.{{0,120}}\b(?:calendar|event|session|deadline)\b|"
    r"\b(?:create|add)\b.{0,120}\bcalendar\s+event\b",
    re.IGNORECASE,
)
_NEGATED_CALENDAR_ACTION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|should\s+not|shouldn't|cannot|can't)\b"
    r"[^.!?;\r\n]{0,48}\b"
    r"(?:put|add|create|schedule|move|reschedule|cancel|delete|remove|update)\b",
    re.IGNORECASE,
)
_DURATION_AMOUNT = (
    r"(?:(?<![\w.])\d{1,2}(?![\w.])|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve)"
)
_DURATION_UNIT = r"(?:hours?|hrs?|minutes?|mins?)"
_DURATION = re.compile(
    rf"\b(?:for\s+)?(?P<amount>{_DURATION_AMOUNT})\s*(?P<unit>{_DURATION_UNIT})\b",
    re.IGNORECASE,
)
_AMBIGUOUS_DURATION = re.compile(
    rf"\b(?:for\s+)?{_DURATION_AMOUNT}\s*(?:or|and|to|[-–])\s*"
    rf"{_DURATION_AMOUNT}\s*{_DURATION_UNIT}\b",
    re.IGNORECASE,
)
_DURATION_UNIT_REFERENCE = re.compile(rf"\b{_DURATION_UNIT}\b", re.IGNORECASE)
_TOMORROW_TIME = re.compile(
    r"\btomorrow\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>[0-5]\d))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_ABSOLUTE_TIME = re.compile(
    r"\bon\s+(?P<date>\d{4}-\d{2}-\d{2})\s+at\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>[0-5]\d))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_EXPLICIT_PROVIDER_TIMEZONE = re.compile(
    r"\b(?:PST|PDT|MST|MDT|CST|CDT|EST|EDT|UTC|GMT)\b",
    re.IGNORECASE,
)
_EXPLICIT_IANA_TIMEZONE = re.compile(r"\b[A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)+\b")
_EXPLICIT_NUMERIC_TIMEZONE_OFFSET = re.compile(
    r"(?:\bUTC\s*|(?<![\w]))[+-]\d{1,2}(?::?\d{2})?(?!\w)",
    re.IGNORECASE,
)
_ASSIGNMENT_TIME_OVERRIDE = re.compile(
    r"\b(?:today|tomorrow|tonight|morning|afternoon|evening|next\s+week|this\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\bat\s+\d{1,2}(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b|"
    r"\bon\s+\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_UNSUPPORTED_CALENDAR_OPERATION = re.compile(
    r"\b(?:move|reschedule|cancel|delete|remove|update)\b.{0,120}"
    r"\b(?:calendar|event|session)\b|"
    r"\b(?:calendar|event|session)\b.{0,120}"
    r"\b(?:move|reschedule|cancel|delete|remove|update)\b",
    re.IGNORECASE,
)
_CALENDAR_ACTION_REFERENCE = re.compile(
    r"\b(?:put|add|create|schedule|move|reschedule|cancel|delete|remove|update)\b"
    r".{0,120}\b(?:calendar|event|session|deadline)\b|"
    r"\b(?:calendar|event|session|deadline)\b.{0,120}"
    r"\b(?:put|add|create|schedule|move|reschedule|cancel|delete|remove|update)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_ACTION_AT_START = re.compile(
    r"\s*(?:please\s+)?(?:move|reschedule|cancel|delete|remove)\b",
    re.IGNORECASE,
)
_UPDATE_ACTION_AT_START = re.compile(r"\s*(?:please\s+)?update\b", re.IGNORECASE)
_TEMPORAL_MARKER = re.compile(
    r"\b(?:today|tomorrow|tonight|morning|afternoon|evening|next\s+week|this\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b(?:at\s+)?\d{1,2}(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


class CalendarIntentError(ValueError):
    """A request cannot be converted to one deterministic, bounded event."""


@dataclass(frozen=True, slots=True)
class CalendarEventPlan:
    request: CalendarCreateRequest
    source_entity_kind: Literal["assignment", "study_task"]
    source_entity_id: int
    duration_note: str


@dataclass(frozen=True, slots=True)
class CalendarProposalResult:
    """Safe Ask projection of the existing task/approval record."""

    proposal_id: str
    approval_id: str
    operation: Literal["CREATE_EVENT"]
    summary: str
    start: str
    end: str
    timezone: str
    source_entity_kind: Literal["assignment", "study_task"]
    source_entity_id: int
    approval_status: str
    task_status: str
    operation_state: str | None

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "approval_id",
            "summary",
            "start",
            "end",
            "timezone",
            "approval_status",
            "task_status",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"{name} must be bounded non-blank text")
        if self.operation != "CREATE_EVENT":
            raise ValueError("only CREATE_EVENT is supported")
        if self.source_entity_kind not in {"assignment", "study_task"}:
            raise ValueError("unsupported Calendar source entity kind")
        if (
            isinstance(self.source_entity_id, bool)
            or not isinstance(self.source_entity_id, int)
            or self.source_entity_id <= 0
        ):
            raise ValueError("source_entity_id must be positive")
        if self.operation_state is not None and (
            not isinstance(self.operation_state, str) or len(self.operation_state) > 64
        ):
            raise ValueError("operation_state must be bounded text or None")


def has_explicit_calendar_create_intent(
    question: str,
    *,
    source_entity_kind: str | None = None,
) -> bool:
    """Recognize only imperative create/schedule phrasing for this bounded tool."""

    if not isinstance(question, str) or not question.strip():
        return False
    if _NEGATED_CALENDAR_ACTION.search(question) or _UNSUPPORTED_CALENDAR_OPERATION.search(
        question
    ):
        return False
    if _CALENDAR_DESTINATION.search(question):
        return True
    if source_entity_kind == "study_task" and re.match(
        r"\s*(?:please\s+)?schedule\b", question, re.I
    ):
        return True
    duration = _DURATION.search(question)
    return duration is not None and bool(
        re.search(r"\bschedule\b", question[: duration.start()], re.IGNORECASE)
    )


def has_calendar_action_reference(
    question: str,
    *,
    source_entity_kind: str | None = None,
) -> bool:
    """Suppress unrelated local writes when a Calendar operation is requested."""

    if not isinstance(question, str) or not question.strip():
        return False
    if _NEGATED_CALENDAR_ACTION.search(question):
        return True
    return bool(
        _CALENDAR_ACTION_REFERENCE.search(question)
        or _UNSUPPORTED_ACTION_AT_START.match(question)
        or (_UPDATE_ACTION_AT_START.match(question) and _TEMPORAL_MARKER.search(question))
        or (
            source_entity_kind == "study_task"
            and re.match(
                r"\s*(?:please\s+)?(?:schedule|move|reschedule|cancel|delete|remove)\b",
                question,
                re.IGNORECASE,
            )
        )
    )


def build_calendar_event_plan(
    question: str,
    *,
    assignment: AssignmentSummary | None,
    study_task: StudyTask | None,
    timezone_name: str,
    now: datetime,
) -> CalendarEventPlan:
    """Derive event identity and time from trusted source data and user syntax."""

    if (assignment is None) == (study_task is None):
        raise CalendarIntentError("one trusted assignment or StudyTask is required")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CalendarIntentError("trusted clock must be timezone-aware")
    try:
        zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError, KeyError) as error:
        raise CalendarIntentError("trusted Calendar timezone is invalid") from error
    if (
        _EXPLICIT_PROVIDER_TIMEZONE.search(question)
        or _EXPLICIT_IANA_TIMEZONE.search(question)
        or _EXPLICIT_NUMERIC_TIMEZONE_OFFSET.search(question)
    ):
        raise CalendarIntentError("explicit timezone conflicts need clarification")

    if assignment is not None:
        if _ASSIGNMENT_TIME_OVERRIDE.search(question):
            raise CalendarIntentError(
                "assignment deadline proposals use the canonical due time only"
            )
        due_at = assignment.due_at
        if due_at is None or due_at.tzinfo is None or due_at.utcoffset() is None:
            raise CalendarIntentError("assignment has no trusted due date")
        start = due_at.astimezone(zone)
        if start.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
            raise CalendarIntentError("assignment deadline is no longer in the future")
        duration = timedelta(minutes=15)
        summary = _event_title("Assignment deadline", assignment.title)
        note = "The proposal uses the canonical assignment due time and a 15-minute marker."
        entity_id = assignment.id
        kind: Literal["assignment", "study_task"] = "assignment"
    else:
        assert study_task is not None
        if study_task.status is not StudyTaskStatus.OPEN:
            raise CalendarIntentError("completed StudyTasks cannot be scheduled")
        start = _parse_local_start(question, zone, now)
        duration, duration_was_defaulted = _session_duration(question)
        summary = _event_title("Study session", study_task.title)
        note = (
            "The proposal uses a one-hour default session duration."
            if duration_was_defaulted
            else "The proposal uses the duration specified in your request."
        )
        entity_id = study_task.id
        kind = "study_task"

    end = (start.astimezone(timezone.utc) + duration).astimezone(zone)
    return CalendarEventPlan(
        CalendarCreateRequest(
            summary=summary,
            start=start,
            end=end,
            timezone=zone.key,
        ),
        kind,
        entity_id,
        note,
    )


def calendar_proposal_from_task(
    task: TaskDetail,
    *,
    plan: CalendarEventPlan,
) -> CalendarProposalResult:
    """Project a pending or completed canonical task without provider internals."""

    if not isinstance(task, TaskDetail) or task.task_kind.value != "CALENDAR_CREATE_EVENT":
        raise CalendarIntentError("Calendar proposal service returned an invalid task")
    approval = task.approval
    if approval is None or approval.action.action_type != "CREATE_EVENT":
        raise CalendarIntentError("Calendar proposal has no matching approval record")
    expected_start = (
        plan.request.start.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    expected_end = (
        plan.request.end.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    if (
        approval.task_id != task.task_id
        or approval.action != task.action
        or task.action.action_type != "CREATE_EVENT"
        or task.action.integration_label != "Google Calendar"
        or task.action.summary != plan.request.summary
        or task.action.start != expected_start
        or task.action.end != expected_end
        or task.action.timezone != plan.request.timezone
        or task.action.description != plan.request.description
        or task.action.location != plan.request.location
    ):
        raise CalendarIntentError("Calendar proposal does not match the trusted request")
    return CalendarProposalResult(
        proposal_id=task.task_id,
        approval_id=approval.approval_id,
        operation="CREATE_EVENT",
        summary=task.action.summary,
        start=task.action.start,
        end=task.action.end,
        timezone=task.action.timezone,
        source_entity_kind=plan.source_entity_kind,
        source_entity_id=plan.source_entity_id,
        approval_status=approval.status.value,
        task_status=task.status.value,
        operation_state=task.operation_state.value if task.operation_state else None,
    )


def _event_title(prefix: str, title: str) -> str:
    summary = unicodedata.normalize("NFKC", f"{prefix}: {title.strip()}")
    if not title.strip() or len(summary) > 200:
        raise CalendarIntentError("trusted title exceeds the Calendar event title limit")
    return summary


def _session_duration(question: str) -> tuple[timedelta, bool]:
    if _AMBIGUOUS_DURATION.search(question):
        raise CalendarIntentError("conflicting session durations need clarification")
    matches = tuple(_DURATION.finditer(question))
    if not matches:
        if _DURATION_UNIT_REFERENCE.search(question):
            raise CalendarIntentError("session duration syntax is unsupported")
        return timedelta(hours=1), True
    if len(matches) != 1:
        raise CalendarIntentError("conflicting session durations need clarification")
    match = matches[0]
    amount_text = match.group("amount").casefold()
    amount = int(amount_text) if amount_text.isdigit() else _NUMBER_WORDS[amount_text]
    unit = match.group("unit").casefold()
    duration = (
        timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(minutes=amount)
    )
    if duration <= timedelta(0) or duration > timedelta(hours=24):
        raise CalendarIntentError("session duration must be at most 24 hours")
    return duration, False


def _parse_local_start(question: str, zone: ZoneInfo, now: datetime) -> datetime:
    tomorrow_matches = tuple(_TOMORROW_TIME.finditer(question))
    absolute_matches = tuple(_ABSOLUTE_TIME.finditer(question))
    matches = (*tomorrow_matches, *absolute_matches)
    if len(matches) != 1:
        raise CalendarIntentError("one unambiguous date and time are required")
    match = matches[0]
    remainder = question[: match.start()] + " " + question[match.end() :]
    if _TEMPORAL_MARKER.search(remainder):
        raise CalendarIntentError("conflicting date or time expressions need clarification")
    if tomorrow_matches:
        target_date = now.astimezone(zone).date() + timedelta(days=1)
    else:
        try:
            target_date = date.fromisoformat(match.group("date"))
        except ValueError as error:
            raise CalendarIntentError("requested date is invalid") from error
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    ampm = match.group("ampm").replace(".", "").casefold()
    if not 1 <= hour <= 12:
        raise CalendarIntentError("12-hour time is invalid")
    hour = hour % 12 + (12 if ampm == "pm" else 0)
    wall_time = datetime.combine(target_date, time(hour, minute))
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = wall_time.replace(tzinfo=zone, fold=fold)
        utc_candidate = candidate.astimezone(timezone.utc)
        round_trip = utc_candidate.astimezone(zone)
        if round_trip.replace(tzinfo=None) == wall_time:
            candidates[utc_candidate] = candidate
    if len(candidates) != 1:
        raise CalendarIntentError("requested wall time is nonexistent or ambiguous")
    result = next(iter(candidates.values()))
    if result.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        raise CalendarIntentError("requested Calendar start must be in the future")
    return result


__all__ = [
    "CalendarEventPlan",
    "CalendarIntentError",
    "CalendarProposalResult",
    "build_calendar_event_plan",
    "calendar_proposal_from_task",
    "has_calendar_action_reference",
    "has_explicit_calendar_create_intent",
]
