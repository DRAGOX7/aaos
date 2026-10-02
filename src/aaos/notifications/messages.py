"""Fixed Telegram message templates (Phase 10 spec §5).

Every value from Moodle is HTML-escaped into a fixed template, so it stays
text: never markup, never an instruction. Links are the configured Moodle
origin plus a path built from a numeric stored ID.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aaos.notifications.events import NotificationKind

TELEGRAM_LIMIT = 4096
AMMAN = ZoneInfo("Asia/Amman")

_HEADINGS = {
    NotificationKind.NEW_ANNOUNCEMENT: "New Announcement",
    NotificationKind.NEW_ASSIGNMENT: "New Assignment",
    NotificationKind.DEADLINE_CHANGED: "Deadline Changed",
    NotificationKind.NEW_COURSE: "New Course",
    NotificationKind.COURSE_ARCHIVED: "Course Archived",
    NotificationKind.AUTH_REQUIRED: "Moodle Login Needed",
}


def format_time(value: str | None, missing: str) -> str:
    if not value:
        return missing
    try:
        moment = datetime.fromisoformat(value).astimezone(AMMAN)
    except ValueError:
        return missing
    return f"{moment:%b} {moment.day}, {moment:%Y} — {moment:%H:%M}"


def _text(value: object) -> str:
    return escape(str(value), quote=False) if value else ""


def _link(origin: str, path: object) -> str:
    if not isinstance(path, str) or not path.startswith("/current/"):
        return ""
    return f'\n\n<a href="{escape(origin + path, quote=True)}">Open in Moodle</a>'


def _cap(message: str) -> str:
    return message if len(message) <= TELEGRAM_LIMIT else message[: TELEGRAM_LIMIT - 1] + "…"


def render(kind: NotificationKind, payload: Mapping[str, object], *, origin: str) -> str:
    """One notification as Telegram HTML."""
    heading = f"<b>AAOS — {_HEADINGS[kind]}</b>"
    course = _text(payload.get("course"))
    title = _text(payload.get("title"))
    if kind is NotificationKind.NEW_ANNOUNCEMENT:
        body = (
            f"{course}\n\n{title}\n\n"
            f"Published: {format_time(_str(payload, 'published_at'), 'date not stored')}"
        )
    elif kind is NotificationKind.NEW_ASSIGNMENT:
        body = f"{course}\n\n{title}\n\nDue: {format_time(_str(payload, 'due_at'), 'no due date')}"
    elif kind is NotificationKind.DEADLINE_CHANGED:
        body = (
            f"{course}\n\n{title}\n\n"
            f"Before: {format_time(_str(payload, 'previous_due_at'), 'no due date')}\n"
            f"Now: {format_time(_str(payload, 'due_at'), 'no due date')}"
        )
    elif kind is NotificationKind.NEW_COURSE:
        body = f"{course}\n\nA new course is now available in your Moodle account."
    elif kind is NotificationKind.COURSE_ARCHIVED:
        body = (
            f"{course}\n\nThis course is no longer listed in Moodle. "
            "Its indexed content stays searchable as a past course."
        )
    else:
        body = (
            "AAOS could not sync Moodle because the login session expired. "
            "Run the Moodle login once to resume."
        )
    return _cap(f"{heading}\n\n{body}{_link(origin, payload.get('path'))}")


def render_digest(
    items: Sequence[tuple[NotificationKind, Mapping[str, object]]], *, origin: str
) -> str:
    """Several notifications from one run as one grouped message."""
    lines = [f"<b>AAOS — {len(items)} changes in your Moodle</b>", ""]
    for kind, payload in items:
        parts = [_HEADINGS[kind], _text(payload.get("course"))]
        title = _text(payload.get("title"))
        if title:
            parts.append(title)
        if kind in (NotificationKind.NEW_ASSIGNMENT, NotificationKind.DEADLINE_CHANGED):
            parts.append("due " + format_time(_str(payload, "due_at"), "not set"))
        lines.append("• " + " · ".join(part for part in parts if part))
    return _cap("\n".join(lines))


def _str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None
