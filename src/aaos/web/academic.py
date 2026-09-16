"""Read-only browser projections for academic data."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import CourseORM
from aaos.web.contracts import CourseSummary


def _to_utc(value: datetime | None) -> datetime | None:
    """Normalize a persisted aware timestamp to canonical UTC."""

    if value is None:
        return None

    if value.tzinfo is None:
        raise ValueError("Persisted course timestamp must be timezone-aware.")

    return value.astimezone(timezone.utc)


class AcademicReadService:
    """Read-only Phase 7 projection service for academic state."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_courses(self) -> tuple[CourseSummary, ...]:
        """Return active courses using stable browser-visible ordering."""

        statement = (
            select(CourseORM)
            .where(CourseORM.is_active.is_(True))
            .order_by(
                case((CourseORM.code.is_(None), 1), else_=0),
                func.lower(CourseORM.code),
                func.lower(CourseORM.title),
                CourseORM.id,
            )
        )

        with self._sessions() as session:
            courses = session.scalars(statement).all()

        return tuple(
            CourseSummary(
                id=course.id,
                title=course.title,
                code=course.code,
                description=course.description,
                updated_at=_to_utc(course.source_updated_at),
            )
            for course in courses
        )
