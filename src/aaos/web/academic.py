"""Read-only browser projections for academic data."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import AssignmentORM, CourseORM
from aaos.web.contracts import AssignmentSummary, CourseSummary
from aaos.web.errors import Phase7ApiError

MAX_AGENT_READ_LIMIT = 51


def _validated_read_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= MAX_AGENT_READ_LIMIT)
    ):
        raise ValueError("read limit must be between one and 51")
    return limit


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

    def list_courses(self, *, limit: int | None = None) -> tuple[CourseSummary, ...]:
        """Return active courses using stable browser-visible ordering."""

        limit = _validated_read_limit(limit)
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
        if limit is not None:
            statement = statement.limit(limit)

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

    def get_course(self, course_id: int) -> CourseSummary:
        """Return one active course or a stable non-leaking not-found error."""

        with self._sessions() as session:
            course = session.scalar(
                select(CourseORM).where(
                    CourseORM.id == course_id,
                    CourseORM.is_active.is_(True),
                )
            )

        if course is None:
            raise Phase7ApiError(
                404,
                "COURSE_NOT_FOUND",
                "The requested course is not available.",
            )

        return CourseSummary(
            id=course.id,
            title=course.title,
            code=course.code,
            description=course.description,
            updated_at=_to_utc(course.source_updated_at),
        )

    def list_assignments(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        """Return active assignments with stable due-date ordering."""

        limit = _validated_read_limit(limit)
        if course_id is not None:
            self.get_course(course_id)

        statement = (
            select(AssignmentORM)
            .join(CourseORM, CourseORM.id == AssignmentORM.course_id)
            .where(
                AssignmentORM.is_active.is_(True),
                CourseORM.is_active.is_(True),
            )
            .order_by(
                case((AssignmentORM.due_at.is_(None), 1), else_=0),
                AssignmentORM.due_at,
                AssignmentORM.id,
            )
        )

        if course_id is not None:
            statement = statement.where(AssignmentORM.course_id == course_id)
        if limit is not None:
            statement = statement.limit(limit)

        with self._sessions() as session:
            assignments = session.scalars(statement).all()

        return tuple(
            AssignmentSummary(
                id=assignment.id,
                course_id=assignment.course_id,
                title=assignment.title,
                description=assignment.description,
                due_at=_to_utc(assignment.due_at),
                updated_at=_to_utc(assignment.source_updated_at),
            )
            for assignment in assignments
        )

    def get_assignment(self, assignment_id: int) -> AssignmentSummary:
        """Return one active assignment or a stable non-leaking not-found error."""

        if (
            isinstance(assignment_id, bool)
            or not isinstance(assignment_id, int)
            or assignment_id <= 0
        ):
            raise Phase7ApiError(
                422,
                "INVALID_REQUEST",
                "The assignment identifier is invalid.",
            )

        statement = (
            select(AssignmentORM)
            .join(CourseORM, CourseORM.id == AssignmentORM.course_id)
            .where(
                AssignmentORM.id == assignment_id,
                AssignmentORM.is_active.is_(True),
                CourseORM.is_active.is_(True),
            )
        )
        with self._sessions() as session:
            assignment = session.scalar(statement)

        if assignment is None:
            raise Phase7ApiError(
                404,
                "ASSIGNMENT_NOT_FOUND",
                "The requested assignment is not available.",
            )

        return AssignmentSummary(
            id=assignment.id,
            course_id=assignment.course_id,
            title=assignment.title,
            description=assignment.description,
            due_at=_to_utc(assignment.due_at),
            updated_at=_to_utc(assignment.source_updated_at),
        )
