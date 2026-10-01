"""Phase I2: trusted, bounded announcement reads."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import AnnouncementORM, Base, CourseORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.web.academic import AcademicReadService
from aaos.web.contracts import AnnouncementSummary

T0 = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_database_engine(tmp_path / "announcements.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _course(course_id: int, *, active: bool = True) -> CourseORM:
    return CourseORM(
        id=course_id,
        source_system="moodle",
        source_id=f"course-{course_id}",
        title=f"Course {course_id}",
        code=None,
        description=None,
        is_active=active,
    )


def _announcement(
    announcement_id: int,
    course_id: int,
    title: str,
    *,
    body: str | None = None,
    published_at: datetime | None = None,
    active: bool = True,
) -> AnnouncementORM:
    return AnnouncementORM(
        id=announcement_id,
        course_id=course_id,
        source_system="moodle",
        source_id=f"announcement-{announcement_id}",
        title=title,
        body=body,
        published_at=published_at,
        is_active=active,
    )


def _store(sessions: sessionmaker[Session], *rows: object) -> None:
    with sessions() as session:
        session.add_all(rows)
        session.commit()


def test_announcements_are_active_scoped_and_newest_first(
    sessions: sessionmaker[Session],
) -> None:
    _store(sessions, _course(1), _course(2), _course(3, active=False))
    _store(
        sessions,
        _announcement(10, 1, "older", published_at=T0),
        _announcement(11, 1, "newer", body="Exam moved", published_at=T0 + timedelta(days=2)),
        _announcement(12, 1, "undated"),
        _announcement(13, 1, "withdrawn", published_at=T0, active=False),
        _announcement(14, 2, "other course", published_at=T0),
        _announcement(15, 3, "inactive course", published_at=T0),
    )
    service = AcademicReadService(sessions)

    scoped = service.list_announcements((1,))
    everything = service.list_announcements()

    assert [item.title for item in scoped] == ["newer", "older", "undated"]
    assert scoped[0] == AnnouncementSummary(
        id=11,
        course_id=1,
        title="newer",
        body="Exam moved",
        published_at=T0 + timedelta(days=2),
    )
    assert {item.id for item in everything} == {10, 11, 12, 14}


def test_blank_bodies_are_reported_as_not_stored(sessions: sessionmaker[Session]) -> None:
    _store(sessions, _course(1))
    _store(sessions, _announcement(10, 1, "title only", body="   "))

    (item,) = AcademicReadService(sessions).list_announcements((1,))

    assert item.body is None
    assert item.published_at is None


def test_announcement_reads_are_bounded_and_validate_limits(
    sessions: sessionmaker[Session],
) -> None:
    _store(sessions, _course(1))
    _store(sessions, *(_announcement(100 + index, 1, f"item {index}") for index in range(5)))
    service = AcademicReadService(sessions)

    assert len(service.list_announcements((1,), limit=3)) == 3
    with pytest.raises(ValueError, match="read limit"):
        service.list_announcements((1,), limit=0)
