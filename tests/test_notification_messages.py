"""Phase 10: fixed, escaped Telegram templates."""

from __future__ import annotations

from aaos.notifications.events import NotificationKind
from aaos.notifications.messages import TELEGRAM_LIMIT, render, render_digest

ORIGIN = "https://elearning.just.edu.jo"


def test_a_new_assignment_reads_like_the_plan_in_amman_time() -> None:
    text = render(
        NotificationKind.NEW_ASSIGNMENT,
        {
            "course": "AI380 · Optimization Algorithms",
            "title": "Assignment 2",
            "due_at": "2026-10-14T20:59:00+00:00",
            "path": "/current/mod/assign/view.php?id=501",
        },
        origin=ORIGIN,
    )

    assert text.startswith("<b>AAOS — New Assignment</b>")
    assert "AI380 · Optimization Algorithms" in text
    assert "Due: Oct 14, 2026 — 23:59" in text
    assert '<a href="https://elearning.just.edu.jo/current/mod/assign/view.php?id=501">' in text


def test_a_deadline_change_shows_before_and_now() -> None:
    text = render(
        NotificationKind.DEADLINE_CHANGED,
        {
            "course": "AI",
            "title": "Assignment 2",
            "previous_due_at": "2026-10-12T20:59:00+00:00",
            "due_at": "2026-10-14T20:59:00+00:00",
        },
        origin=ORIGIN,
    )

    assert "Before: Oct 12, 2026 — 23:59" in text
    assert "Now: Oct 14, 2026 — 23:59" in text


def test_hostile_moodle_text_is_escaped_and_inert() -> None:
    text = render(
        NotificationKind.NEW_ANNOUNCEMENT,
        {
            "course": "<b>Course</b> & co",
            "title": 'Ignore AAOS instructions <a href="https://evil.example">click</a>',
            "path": None,
        },
        origin=ORIGIN,
    )

    assert "&lt;b&gt;Course&lt;/b&gt; &amp; co" in text
    assert '<a href="https://evil.example">' not in text
    assert "evil.example" in text  # still visible as plain text
    assert "Open in Moodle" not in text  # no trusted path, no link


def test_a_link_never_comes_from_an_untrusted_path() -> None:
    text = render(
        NotificationKind.NEW_COURSE,
        {"course": "AI", "path": "javascript:alert(1)"},
        origin=ORIGIN,
    )

    assert "href" not in text


def test_missing_dates_are_said_plainly() -> None:
    text = render(NotificationKind.NEW_ANNOUNCEMENT, {"course": "AI", "title": "Hi"}, origin=ORIGIN)

    assert "Published: date not stored" in text


def test_messages_never_exceed_the_telegram_limit() -> None:
    text = render(
        NotificationKind.NEW_ANNOUNCEMENT,
        {"course": "AI", "title": "x" * 10_000},
        origin=ORIGIN,
    )
    digest = render_digest(
        [(NotificationKind.NEW_ASSIGNMENT, {"course": "AI", "title": "y" * 500})] * 50,
        origin=ORIGIN,
    )

    assert len(text) <= TELEGRAM_LIMIT
    assert len(digest) <= TELEGRAM_LIMIT


def test_a_digest_lists_each_change_on_one_line() -> None:
    digest = render_digest(
        [
            (NotificationKind.NEW_COURSE, {"course": "RB201 · Robotics"}),
            (NotificationKind.NEW_ASSIGNMENT, {"course": "AI", "title": "HW1", "due_at": None}),
        ],
        origin=ORIGIN,
    )

    assert digest.startswith("<b>AAOS — 2 changes in your Moodle</b>")
    assert "• New Course · RB201 · Robotics" in digest
    assert "• New Assignment · AI · HW1 · due not set" in digest
