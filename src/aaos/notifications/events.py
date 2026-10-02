"""Notification events derived from a complete Moodle snapshot pair (Phase 10, spec §2).

Events come only from the existing deterministic diff. Each carries a stable
key, so the same change is delivered once however many pairs report it, and
a payload of trusted, bounded fields: never Moodle prose beyond a title.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from aaos.agent.course_names import course_display_title
from aaos.moodle.changes import (
    ChangeField,
    ChangeKind,
    EntityType,
    MoodleChange,
    NormalizedMoodleState,
    diff_moodle_snapshots,
)

MAX_TITLE = 300
MAX_COURSE = 200


class NotificationKind(StrEnum):
    NEW_ANNOUNCEMENT = "NEW_ANNOUNCEMENT"
    NEW_ASSIGNMENT = "NEW_ASSIGNMENT"
    DEADLINE_CHANGED = "DEADLINE_CHANGED"
    NEW_COURSE = "NEW_COURSE"
    COURSE_ARCHIVED = "COURSE_ARCHIVED"
    AUTH_REQUIRED = "AUTH_REQUIRED"


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    kind: NotificationKind
    event_key: str
    source_system: str
    course_source_id: str | None
    entity_source_id: str | None
    from_snapshot_id: int | None
    to_snapshot_id: int | None
    payload: Mapping[str, str | None]


_DIGITS = re.compile(r"\d{1,12}")
_PATHS = {
    "COURSE": ("", "/current/course/view.php?id={}"),
    "ASSIGNMENT": ("assign-cm:", "/current/mod/assign/view.php?id={}"),
    "ANNOUNCEMENT": ("", "/current/mod/forum/discuss.php?d={}"),
}


def moodle_path(entity_type: str, source_id: str) -> str | None:
    """A Moodle page path built only from a numeric stored ID, never from content."""
    rule = _PATHS.get(entity_type)
    if rule is None:
        return None
    prefix, template = rule
    if not source_id.startswith(prefix):
        return None
    identifier = source_id[len(prefix) :]
    return template.format(identifier) if _DIGITS.fullmatch(identifier) else None


def event_key(kind: NotificationKind, source_system: str, *parts: str) -> str:
    canonical = json.dumps([kind.value, source_system, *parts], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bounded(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def derive_events(
    previous: NormalizedMoodleState,
    current: NormalizedMoodleState,
    *,
    from_snapshot_id: int,
    to_snapshot_id: int,
    detected_at: datetime,
) -> tuple[NotificationEvent, ...]:
    """Events for one COMPLETE→COMPLETE pair; the caller guarantees both are complete."""
    changes = diff_moodle_snapshots(
        previous,
        current,
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
        detected_at=detected_at,
    )
    titles = {course.source_id: course.title for course in (*previous.courses, *current.courses)}
    source = current.source_system
    events: list[NotificationEvent] = []
    for change in changes:
        event = _event_for(change, source, titles, from_snapshot_id, to_snapshot_id)
        if event is not None:
            events.append(event)
    return tuple(events)


def _event_for(
    change: MoodleChange,
    source: str,
    course_titles: Mapping[str, str],
    from_id: int,
    to_id: int,
) -> NotificationEvent | None:
    course = _bounded(
        course_display_title(course_titles.get(change.course_source_id, "")), MAX_COURSE
    )
    title = _bounded(change.after_title or change.before_title, MAX_TITLE)
    entity = change.entity_type.value

    def build(
        kind: NotificationKind, key_parts: tuple[str, ...], **payload: str | None
    ) -> NotificationEvent:
        return NotificationEvent(
            kind=kind,
            event_key=event_key(kind, source, *key_parts),
            source_system=source,
            course_source_id=change.course_source_id,
            entity_source_id=change.entity_source_id,
            from_snapshot_id=from_id,
            to_snapshot_id=to_id,
            payload=MappingProxyType(
                {"course": course, "path": moodle_path(entity, change.entity_source_id), **payload}
            ),
        )

    if change.entity_type is EntityType.COURSE:
        if change.change_kind is ChangeKind.ADDED:
            return build(NotificationKind.NEW_COURSE, (change.entity_source_id,))
        if change.change_kind is ChangeKind.REMOVED:
            # A complete sync no longer lists the course: it is archived. Its
            # items leave with it and are not notified one by one.
            return build(NotificationKind.COURSE_ARCHIVED, (change.entity_source_id, str(to_id)))
        return None
    if change.change_kind is ChangeKind.ADDED and change.entity_type is EntityType.ANNOUNCEMENT:
        return build(
            NotificationKind.NEW_ANNOUNCEMENT,
            (change.entity_source_id,),
            title=title,
            published_at=_iso(change.after_published_at),
        )
    if change.entity_type is EntityType.ASSIGNMENT:
        if change.change_kind is ChangeKind.ADDED:
            return build(
                NotificationKind.NEW_ASSIGNMENT,
                (change.entity_source_id,),
                title=title,
                due_at=_iso(change.after_due_at),
            )
        if (
            change.change_kind is ChangeKind.MODIFIED
            and ChangeField.DUE_AT in change.changed_fields
        ):
            new_due = _iso(change.after_due_at)
            return build(
                NotificationKind.DEADLINE_CHANGED,
                (change.entity_source_id, new_due or "none"),
                title=title,
                due_at=new_due,
                previous_due_at=_iso(change.before_due_at),
            )
    return None
