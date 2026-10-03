"""Phase 10 slice 10C: outbox, de-duplication, baseline, digest and retry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
)
from aaos.moodle.integration import build_isolated_database, sync_moodle_snapshot
from aaos.notifications.adapter import DeliveryError, DeliveryResult
from aaos.notifications.pipeline import NotificationPipeline
from aaos.persistence.notification_models import NotificationDeliveryORM

SOURCE = "moodle:just:fall-2026"
ORIGIN = "https://elearning.just.edu.jo"
AI = RawCourse("3818", "AI380-OPTIMIZATION ALGORITHMS (All Sections )")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeTelegram:
    def __init__(self, *results: DeliveryResult) -> None:
        self.results = list(results)
        self.messages: list[str] = []

    def send(self, html_message: str) -> DeliveryResult:
        self.messages.append(html_message)
        return self.results.pop(0) if self.results else DeliveryResult(sent=True)


@pytest.fixture
def world(tmp_path: Path):
    engine, sessions = build_isolated_database(tmp_path / "notify.db")
    clock = Clock()
    pipeline = NotificationPipeline(sessions, clock=clock, pause=lambda _s: None)
    yield sessions, clock, pipeline
    engine.dispose()


def sync(sessions: sessionmaker[Session], clock: Clock, **items: object) -> None:
    clock.advance(900)
    sync_moodle_snapshot(
        CompleteSourceSnapshot(
            source_system=SOURCE,
            courses=items.get("courses", (AI,)),  # type: ignore[arg-type]
            assignments=items.get("assignments", ()),  # type: ignore[arg-type]
            announcements=items.get("announcements", ()),  # type: ignore[arg-type]
        ),
        session_factory=sessions,
        started_at=clock.now,
        finished_at=clock.now,
    )


POST = RawAnnouncement("9001", "3818", "Midterm Exam Information")
HW = RawAssignment("assign-cm:501", "3818", "Assignment 3", due_at="2026-10-12T20:59:00Z")


def test_nothing_before_enabling_is_notified(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    sync(sessions, clock, announcements=(POST,))
    pipeline.enable(SOURCE)

    assert pipeline.process_snapshots(SOURCE).events_created == 0


def test_a_change_after_enabling_is_sent_once_across_repeated_runs(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    sync(sessions, clock, announcements=(POST,))
    telegram = FakeTelegram()

    for _ in range(4):
        sync(sessions, clock, announcements=(POST,))
        pipeline.process_snapshots(SOURCE)
        pipeline.deliver(telegram, origin=ORIGIN)

    assert len(telegram.messages) == 1
    assert "Midterm Exam Information" in telegram.messages[0]


def test_reprocessing_the_same_pairs_creates_no_duplicates(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    sync(sessions, clock, assignments=(HW,))

    first = pipeline.process_snapshots(SOURCE)
    again = pipeline.process_snapshots(SOURCE)

    assert first.events_created == 1
    assert again.events_created == 0


def test_a_muted_source_records_nothing(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    pipeline.mute(SOURCE)
    sync(sessions, clock, announcements=(POST,))

    assert pipeline.process_snapshots(SOURCE).events_created == 0


def test_enabling_before_any_sync_uses_the_first_sync_as_baseline(world) -> None:
    sessions, clock, pipeline = world
    pipeline.enable(SOURCE)
    sync(sessions, clock, announcements=(POST,))
    assert pipeline.process_snapshots(SOURCE).events_created == 0
    sync(sessions, clock, announcements=(POST,), assignments=(HW,))

    assert pipeline.process_snapshots(SOURCE).events_created == 1


def test_many_changes_from_one_run_arrive_as_one_digest(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    posts = tuple(RawAnnouncement(str(9000 + n), "3818", f"Post {n}") for n in range(7))
    sync(sessions, clock, announcements=posts)
    pipeline.process_snapshots(SOURCE)
    telegram = FakeTelegram()

    counts = pipeline.deliver(telegram, origin=ORIGIN)

    assert len(telegram.messages) == 1
    assert telegram.messages[0].startswith("<b>AAOS — 7 changes in your Moodle</b>")
    assert (counts.sent, counts.suppressed) == (1, 6)


def test_a_failed_send_retries_with_backoff_then_gives_up(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    sync(sessions, clock, announcements=(POST,))
    pipeline.process_snapshots(SOURCE)
    telegram = FakeTelegram(*[DeliveryResult(sent=False, error=DeliveryError.NETWORK)] * 5)

    pipeline.deliver(telegram, origin=ORIGIN)
    assert len(telegram.messages) == 1
    pipeline.deliver(telegram, origin=ORIGIN)  # not due yet
    assert len(telegram.messages) == 1
    for wait in (60, 300, 900, 3600):
        clock.advance(wait)
        pipeline.deliver(telegram, origin=ORIGIN)

    with sessions() as session:
        delivery = session.scalars(select(NotificationDeliveryORM)).one()
    assert len(telegram.messages) == 5
    assert (delivery.status, delivery.attempts, delivery.last_error_code) == (
        "FAILED",
        5,
        "NETWORK",
    )


def test_a_permanent_error_fails_at_once(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    sync(sessions, clock, announcements=(POST,))
    pipeline.process_snapshots(SOURCE)

    counts = pipeline.deliver(
        FakeTelegram(DeliveryResult(sent=False, error=DeliveryError.TOKEN_INVALID)), origin=ORIGIN
    )

    assert counts.failed == 1


def test_telegram_retry_after_is_honoured(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)
    sync(sessions, clock, announcements=(POST,))
    pipeline.process_snapshots(SOURCE)
    telegram = FakeTelegram(
        DeliveryResult(sent=False, error=DeliveryError.RATE_LIMITED, retry_after_seconds=30)
    )

    pipeline.deliver(telegram, origin=ORIGIN)
    clock.advance(29)
    pipeline.deliver(telegram, origin=ORIGIN)
    assert len(telegram.messages) == 1
    clock.advance(1)
    pipeline.deliver(telegram, origin=ORIGIN)

    assert len(telegram.messages) == 2


def test_login_needed_is_sent_once_per_expiry_episode(world) -> None:
    sessions, clock, pipeline = world
    sync(sessions, clock)
    pipeline.enable(SOURCE)

    assert pipeline.record_auth_required(SOURCE) is True
    assert pipeline.record_auth_required(SOURCE) is False  # same episode
    sync(sessions, clock)  # login renewed: a complete sync ends the episode
    assert pipeline.record_auth_required(SOURCE) is True
