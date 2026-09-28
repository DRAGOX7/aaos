"""Closed values for bounded cross-tool study recommendations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal


class StudyActivityType(StrEnum):
    ASSIGNMENT_WORK = "ASSIGNMENT_WORK"
    REVIEW = "REVIEW"
    READING = "READING"
    PRACTICE = "PRACTICE"
    PROJECT_WORK = "PROJECT_WORK"


class StudyRationaleCode(StrEnum):
    DEADLINE_NEAR = "DEADLINE_NEAR"
    OPEN_TASK = "OPEN_TASK"
    RECENT_MOODLE_CHANGE = "RECENT_MOODLE_CHANGE"
    USER_FOCUS = "USER_FOCUS"
    GENERAL_REVIEW = "GENERAL_REVIEW"


class PlanFactKind(StrEnum):
    ASSIGNMENT = "ASSIGNMENT"
    STUDY_TASK = "STUDY_TASK"
    MOODLE_CHANGE = "MOODLE_CHANGE"
    DOCUMENT = "DOCUMENT"


def _positive_id(value: int | None, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


_PLAN_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _validate_plan_id(value: object) -> None:
    if not isinstance(value, str) or _PLAN_ID_RE.fullmatch(value) is None:
        raise ValueError("plan_id must be a request-scoped UUID")


@dataclass(frozen=True, slots=True)
class StudyPlanSourceReference:
    kind: PlanFactKind
    tool_name: str
    entity_id: int | None
    course_id: int | None = None
    change_id: str | None = None
    from_snapshot_id: int | None = None
    to_snapshot_id: int | None = None
    document_version_id: int | None = None
    citation_location: str | None = None
    claim_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PlanFactKind):
            raise TypeError("kind must be a PlanFactKind")
        if not isinstance(self.tool_name, str) or self.tool_name not in {
            "academic.list_assignments",
            "study_task.list",
            "moodle.list_changes",
            "rag.answer",
        }:
            raise ValueError("tool_name must be a supported read source")
        for name in ("entity_id", "course_id", "document_version_id"):
            _positive_id(getattr(self, name), name)
        for name in ("from_snapshot_id", "to_snapshot_id"):
            _positive_id(getattr(self, name), name)
        if self.change_id is not None and (
            not isinstance(self.change_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", self.change_id)
        ):
            raise ValueError("change_id must be a bounded trusted identifier")
        if self.citation_location is not None and (
            not isinstance(self.citation_location, str) or len(self.citation_location) > 256
        ):
            raise ValueError("citation_location must be bounded text or None")
        if self.claim_id is not None and (
            not isinstance(self.claim_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", self.claim_id)
        ):
            raise ValueError("claim_id must be a bounded trusted identifier")


@dataclass(frozen=True, slots=True)
class StudyPlanObligation:
    assignment_id: int
    course_id: int
    title: str
    due_at: datetime | None
    overdue: bool
    source: StudyPlanSourceReference

    def __post_init__(self) -> None:
        _positive_id(self.assignment_id, "assignment_id")
        _positive_id(self.course_id, "course_id")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 512:
            raise ValueError("title must be bounded non-blank text")
        if self.due_at is not None:
            object.__setattr__(self, "due_at", _aware_utc(self.due_at, "due_at"))
        if not isinstance(self.overdue, bool):
            raise TypeError("overdue must be a bool")
        if self.source.kind is not PlanFactKind.ASSIGNMENT:
            raise ValueError("assignment fact must cite an assignment source")
        if self.source.entity_id != self.assignment_id or self.source.course_id != self.course_id:
            raise ValueError("assignment provenance does not match the assignment identity")


@dataclass(frozen=True, slots=True)
class StudyPlanTaskFact:
    task_id: int
    title: str
    status: str
    priority: str
    course_id: int | None
    assignment_id: int | None
    due_at: datetime | None
    source: StudyPlanSourceReference

    def __post_init__(self) -> None:
        _positive_id(self.task_id, "task_id")
        _positive_id(self.course_id, "course_id")
        _positive_id(self.assignment_id, "assignment_id")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 512:
            raise ValueError("title must be bounded non-blank text")
        if not isinstance(self.status, str) or self.status not in {"OPEN", "COMPLETED"}:
            raise ValueError("task status must use a closed value")
        if not isinstance(self.priority, str) or self.priority not in {
            "LOW",
            "NORMAL",
            "HIGH",
        }:
            raise ValueError("task priority must use a closed value")
        if self.due_at is not None:
            object.__setattr__(self, "due_at", _aware_utc(self.due_at, "due_at"))
        if (
            self.source.kind is not PlanFactKind.STUDY_TASK
            or self.source.entity_id != self.task_id
            or self.source.course_id != self.course_id
        ):
            raise ValueError("task provenance does not match the task identity")


@dataclass(frozen=True, slots=True)
class StudyPlanMoodleChange:
    change_id: str
    assignment_id: int | None
    course_id: int | None
    change_kind: str
    title: str | None
    changed_fields: tuple[str, ...]
    before_due_at: datetime | None
    after_due_at: datetime | None
    from_snapshot_id: int
    to_snapshot_id: int
    detected_at: datetime
    source: StudyPlanSourceReference

    def __post_init__(self) -> None:
        if not isinstance(self.change_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", self.change_id
        ):
            raise ValueError("change_id must be a bounded trusted identifier")
        _positive_id(self.assignment_id, "assignment_id")
        _positive_id(self.course_id, "course_id")
        if not isinstance(self.change_kind, str) or self.change_kind not in {
            "ADDED",
            "REMOVED",
            "MODIFIED",
        }:
            raise ValueError("change_kind must use a closed Moodle value")
        if self.title is not None and (not isinstance(self.title, str) or len(self.title) > 512):
            raise ValueError("title must be bounded text or None")
        if not isinstance(self.changed_fields, tuple) or any(
            not isinstance(field, str)
            or field
            not in {"title", "code", "due_at", "published_at", "course", "document_version"}
            for field in self.changed_fields
        ):
            raise ValueError("changed_fields must use closed Moodle values")
        for name in ("before_due_at", "after_due_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware_utc(value, name))
        for name in ("from_snapshot_id", "to_snapshot_id"):
            _positive_id(getattr(self, name), name)
        object.__setattr__(self, "detected_at", _aware_utc(self.detected_at, "detected_at"))
        if (
            self.source.kind is not PlanFactKind.MOODLE_CHANGE
            or self.source.change_id != self.change_id
            or self.source.from_snapshot_id != self.from_snapshot_id
            or self.source.to_snapshot_id != self.to_snapshot_id
        ):
            raise ValueError("change provenance does not match the change identity")


@dataclass(frozen=True, slots=True)
class StudyPlanItem:
    study_date: date
    course_id: int
    assignment_id: int | None
    study_task_id: int | None
    activity_type: StudyActivityType
    duration_minutes: int
    rationale_code: StudyRationaleCode
    focus: str | None
    estimated_duration: bool
    source_refs: tuple[StudyPlanSourceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.study_date, date) or isinstance(self.study_date, datetime):
            raise TypeError("study_date must be a date")
        _positive_id(self.course_id, "course_id")
        _positive_id(self.assignment_id, "assignment_id")
        _positive_id(self.study_task_id, "study_task_id")
        if not isinstance(self.activity_type, StudyActivityType):
            raise TypeError("activity_type must be a StudyActivityType")
        if (
            isinstance(self.duration_minutes, bool)
            or not isinstance(self.duration_minutes, int)
            or not 15 <= self.duration_minutes <= 180
        ):
            raise ValueError("duration_minutes must be between 15 and 180")
        if not isinstance(self.rationale_code, StudyRationaleCode):
            raise TypeError("rationale_code must be a StudyRationaleCode")
        if self.focus is not None and (
            not isinstance(self.focus, str) or not 1 <= len(self.focus) <= 120
        ):
            raise ValueError("focus must be bounded non-blank text or None")
        if not isinstance(self.estimated_duration, bool):
            raise TypeError("estimated_duration must be a bool")
        if (
            not isinstance(self.source_refs, tuple)
            or len(self.source_refs) > 4
            or any(not isinstance(source, StudyPlanSourceReference) for source in self.source_refs)
        ):
            raise ValueError("source_refs must be a bounded tuple of trusted sources")


@dataclass(frozen=True, slots=True)
class StudyPlanCourseReference:
    course_id: int
    title: str

    def __post_init__(self) -> None:
        _positive_id(self.course_id, "course_id")
        if not isinstance(self.title, str) or len(self.title) > 128:
            raise ValueError("title must be bounded text")


@dataclass(frozen=True, slots=True)
class StudyPlanContextItem:
    position: int
    study_date: date
    course_id: int
    assignment_id: int | None
    study_task_id: int | None
    activity_type: StudyActivityType
    duration_minutes: int
    rationale_code: StudyRationaleCode
    due_at: datetime | None = None
    task_status: str | None = None
    task_priority: str | None = None
    moodle_change_id: str | None = None
    change_before_due_at: datetime | None = None
    change_after_due_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, int)
            or not 1 <= self.position <= 12
        ):
            raise ValueError("position must be within the bounded plan")
        if not isinstance(self.study_date, date) or isinstance(self.study_date, datetime):
            raise TypeError("study_date must be a date")
        _positive_id(self.course_id, "course_id")
        _positive_id(self.assignment_id, "assignment_id")
        _positive_id(self.study_task_id, "study_task_id")
        if not isinstance(self.activity_type, StudyActivityType):
            raise TypeError("activity_type must be a StudyActivityType")
        if (
            isinstance(self.duration_minutes, bool)
            or not isinstance(self.duration_minutes, int)
            or not 15 <= self.duration_minutes <= 180
        ):
            raise ValueError("duration_minutes must be bounded")
        if not isinstance(self.rationale_code, StudyRationaleCode):
            raise TypeError("rationale_code must be a StudyRationaleCode")
        if self.task_status is not None and (
            not isinstance(self.task_status, str) or self.task_status not in {"OPEN", "COMPLETED"}
        ):
            raise ValueError("task_status must use a closed value")
        if self.task_priority is not None and (
            not isinstance(self.task_priority, str)
            or self.task_priority not in {"LOW", "NORMAL", "HIGH"}
        ):
            raise ValueError("task_priority must use a closed value")
        for name in ("due_at", "change_before_due_at", "change_after_due_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware_utc(value, name))
        if self.moodle_change_id is not None and (
            not isinstance(self.moodle_change_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}", self.moodle_change_id)
        ):
            raise ValueError("moodle_change_id must be a bounded identifier")


@dataclass(frozen=True, slots=True)
class StudyPlanContext:
    """Small, typed plan snapshot retained only in bounded process-local context."""

    plan_id: str
    window_start: date
    window_end: date
    timezone: str
    generated_at: datetime
    course_refs: tuple[StudyPlanCourseReference, ...]
    items: tuple[StudyPlanContextItem, ...]
    moodle_to_snapshot_id: int | None = None

    def __post_init__(self) -> None:
        _validate_plan_id(self.plan_id)
        if (
            not isinstance(self.window_start, date)
            or isinstance(self.window_start, datetime)
            or not isinstance(self.window_end, date)
            or isinstance(self.window_end, datetime)
        ):
            raise TypeError("plan context window bounds must be dates")
        if self.window_end < self.window_start or (self.window_end - self.window_start).days >= 14:
            raise ValueError("plan context window must be bounded to 14 days")
        if (
            not isinstance(self.timezone, str)
            or not self.timezone.strip()
            or len(self.timezone) > 128
        ):
            raise ValueError("timezone must be bounded non-blank text")
        object.__setattr__(self, "generated_at", _aware_utc(self.generated_at, "generated_at"))
        if (
            not isinstance(self.course_refs, tuple)
            or len(self.course_refs) > 12
            or any(not isinstance(item, StudyPlanCourseReference) for item in self.course_refs)
        ):
            raise ValueError("course_refs must be bounded")
        if (
            not isinstance(self.items, tuple)
            or len(self.items) > 12
            or any(not isinstance(item, StudyPlanContextItem) for item in self.items)
        ):
            raise ValueError("items must be bounded")
        if self.moodle_to_snapshot_id is not None and (
            isinstance(self.moodle_to_snapshot_id, bool)
            or not isinstance(self.moodle_to_snapshot_id, int)
            or self.moodle_to_snapshot_id <= 0
        ):
            raise ValueError("moodle_to_snapshot_id must be positive or None")
        serialized = json.dumps(
            {
                "plan_id": self.plan_id,
                "window_start": self.window_start.isoformat(),
                "window_end": self.window_end.isoformat(),
                "timezone": self.timezone,
                "generated_at": self.generated_at.isoformat(),
                "snapshot": self.moodle_to_snapshot_id,
                "courses": [
                    {"id": course.course_id, "title": course.title} for course in self.course_refs
                ],
                "items": [
                    {
                        "position": item.position,
                        "date": item.study_date.isoformat(),
                        "course_id": item.course_id,
                        "assignment_id": item.assignment_id,
                        "task_id": item.study_task_id,
                        "activity": item.activity_type.value,
                        "minutes": item.duration_minutes,
                        "reason": item.rationale_code.value,
                        "due_at": item.due_at.isoformat() if item.due_at else None,
                        "task_status": item.task_status,
                        "task_priority": item.task_priority,
                        "change_id": item.moodle_change_id,
                        "before_due_at": (
                            item.change_before_due_at.isoformat()
                            if item.change_before_due_at
                            else None
                        ),
                        "after_due_at": (
                            item.change_after_due_at.isoformat()
                            if item.change_after_due_at
                            else None
                        ),
                    }
                    for item in self.items
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(serialized.encode("utf-8")) > 4096:
            raise ValueError("typed plan context exceeds its serialized bound")


@dataclass(frozen=True, slots=True)
class StudyPlan:
    plan_id: str
    planning_window_start: date
    planning_window_end: date
    timezone: str
    generated_at: datetime
    calendar_conflict_aware: Literal[False]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    obligations: tuple[StudyPlanObligation, ...]
    study_tasks: tuple[StudyPlanTaskFact, ...]
    moodle_changes: tuple[StudyPlanMoodleChange, ...]
    items: tuple[StudyPlanItem, ...]
    available_minutes: int | None = None
    max_session_minutes: int | None = None
    moodle_from_snapshot_id: int | None = None
    moodle_to_snapshot_id: int | None = None
    rag_provenance: tuple[StudyPlanSourceReference, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.generated_at, datetime):
            raise TypeError("generated_at must be a datetime")
        object.__setattr__(self, "generated_at", _aware_utc(self.generated_at, "generated_at"))
        _validate_plan_id(self.plan_id)
        if (
            not isinstance(self.planning_window_start, date)
            or isinstance(self.planning_window_start, datetime)
            or not isinstance(self.planning_window_end, date)
            or isinstance(self.planning_window_end, datetime)
        ):
            raise TypeError("planning window bounds must be dates")
        if (
            self.planning_window_end < self.planning_window_start
            or (self.planning_window_end - self.planning_window_start).days >= 14
        ):
            raise ValueError("planning window must be bounded to 14 local calendar days")
        if (
            not isinstance(self.timezone, str)
            or not self.timezone.strip()
            or len(self.timezone) > 128
        ):
            raise ValueError("timezone must be bounded non-blank text")
        if not isinstance(self.calendar_conflict_aware, bool) or self.calendar_conflict_aware:
            raise ValueError("Calendar conflict awareness is unsupported")
        if (
            not isinstance(self.assumptions, tuple)
            or len(self.assumptions) > 8
            or any(not isinstance(value, str) or len(value) > 256 for value in self.assumptions)
        ):
            raise ValueError("assumptions must be bounded text")
        if (
            not isinstance(self.warnings, tuple)
            or len(self.warnings) > 12
            or any(not isinstance(value, str) or len(value) > 512 for value in self.warnings)
        ):
            raise ValueError("warnings must be bounded text")
        for name, values, maximum in (
            ("obligations", self.obligations, 30),
            ("study_tasks", self.study_tasks, 50),
            ("moodle_changes", self.moodle_changes, 5),
            ("items", self.items, 12),
            ("rag_provenance", self.rag_provenance, 64),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise ValueError(f"{name} must remain within its trusted bound")
        if any(not isinstance(value, StudyPlanObligation) for value in self.obligations):
            raise TypeError("obligations must contain trusted assignment facts")
        if any(not isinstance(value, StudyPlanTaskFact) for value in self.study_tasks):
            raise TypeError("study_tasks must contain trusted task facts")
        if any(not isinstance(value, StudyPlanMoodleChange) for value in self.moodle_changes):
            raise TypeError("moodle_changes must contain trusted change facts")
        if any(not isinstance(value, StudyPlanItem) for value in self.items):
            raise TypeError("items must contain validated recommendations")
        if any(not isinstance(value, StudyPlanSourceReference) for value in self.rag_provenance):
            raise TypeError("rag_provenance must contain trusted source references")
        for name in ("available_minutes", "max_session_minutes"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or None")
        for name in ("moodle_from_snapshot_id", "moodle_to_snapshot_id"):
            _positive_id(getattr(self, name), name)

    def context(self, course_refs: tuple[StudyPlanCourseReference, ...]) -> StudyPlanContext:
        obligation_index = {item.assignment_id: item for item in self.obligations}
        task_index = {item.task_id: item for item in self.study_tasks}
        change_index = {
            item.change_id: item for item in self.moodle_changes if item.change_kind != "REMOVED"
        }
        context_items: list[StudyPlanContextItem] = []
        for position, item in enumerate(self.items, start=1):
            obligation = (
                obligation_index.get(item.assignment_id) if item.assignment_id is not None else None
            )
            task = task_index.get(item.study_task_id) if item.study_task_id is not None else None
            change = next(
                (
                    candidate
                    for candidate in change_index.values()
                    if candidate.assignment_id == item.assignment_id
                ),
                None,
            )
            context_items.append(
                StudyPlanContextItem(
                    position=position,
                    study_date=item.study_date,
                    course_id=item.course_id,
                    assignment_id=item.assignment_id,
                    study_task_id=item.study_task_id,
                    activity_type=item.activity_type,
                    duration_minutes=item.duration_minutes,
                    rationale_code=item.rationale_code,
                    due_at=obligation.due_at if obligation is not None else None,
                    task_status=task.status if task is not None else None,
                    task_priority=task.priority if task is not None else None,
                    moodle_change_id=change.change_id if change is not None else None,
                    change_before_due_at=change.before_due_at if change is not None else None,
                    change_after_due_at=change.after_due_at if change is not None else None,
                )
            )
        return StudyPlanContext(
            plan_id=self.plan_id,
            window_start=self.planning_window_start,
            window_end=self.planning_window_end,
            timezone=self.timezone,
            generated_at=self.generated_at,
            course_refs=course_refs,
            items=tuple(context_items),
            moodle_to_snapshot_id=self.moodle_to_snapshot_id,
        )
