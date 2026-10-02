"""Read-only browser projections for academic data."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.visibility import course_visible, item_visible
from aaos.persistence.academic_models import AnnouncementORM, AssignmentORM, CourseORM
from aaos.web.contracts import AnnouncementSummary, AssignmentSummary, CourseSummary
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


def _course_summary(course: CourseORM) -> CourseSummary:
    past = not course.is_active
    return CourseSummary(
        id=course.id,
        title=course.title,
        code=course.code,
        description=course.description,
        updated_at=_to_utc(course.source_updated_at),
        status="past" if past else "current",
        archived_at=_to_utc(course.archived_at) if past else None,
    )


class AcademicReadService:
    """Read-only Phase 7 projection service for academic state."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_courses(self, *, limit: int | None = None) -> tuple[CourseSummary, ...]:
        """Return current courses, then past (archived) ones, in stable order."""

        limit = _validated_read_limit(limit)
        statement = (
            select(CourseORM)
            .where(course_visible(include_past=True))
            .order_by(
                case((CourseORM.is_active.is_(True), 0), else_=1),
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

        return tuple(_course_summary(course) for course in courses)

    def get_course(self, course_id: int) -> CourseSummary:
        """Return one current or past course, or a stable non-leaking not-found error."""

        with self._sessions() as session:
            course = session.scalar(
                select(CourseORM).where(
                    CourseORM.id == course_id,
                    course_visible(include_past=True),
                )
            )

        if course is None:
            raise Phase7ApiError(
                404,
                "COURSE_NOT_FOUND",
                "The requested course is not available.",
            )

        return _course_summary(course)

    def list_assignments(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        """Return assignments with stable due-date ordering.

        Unscoped listings cover current courses only; a named past course
        lists the assignments archived with it.
        """

        limit = _validated_read_limit(limit)
        if course_id is not None:
            self.get_course(course_id)

        statement = (
            select(AssignmentORM)
            .join(CourseORM, CourseORM.id == AssignmentORM.course_id)
            .where(item_visible(AssignmentORM, include_past=course_id is not None))
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

    def list_announcements(
        self,
        course_ids: tuple[int, ...] | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[AnnouncementSummary, ...]:
        """Return announcements, newest stored date first.

        Unscoped listings cover current courses only; past-course announcements
        appear only when their course is named (archived-courses spec).

        Announcements without a stored date follow, in stable id order. A blank
        stored body is reported as not stored (None), never as content.
        """

        limit = _validated_read_limit(limit)
        statement = (
            select(AnnouncementORM)
            .join(CourseORM, CourseORM.id == AnnouncementORM.course_id)
            .where(item_visible(AnnouncementORM, include_past=course_ids is not None))
            .order_by(
                case((AnnouncementORM.published_at.is_(None), 1), else_=0),
                AnnouncementORM.published_at.desc(),
                AnnouncementORM.id,
            )
        )
        if course_ids is not None:
            statement = statement.where(AnnouncementORM.course_id.in_(course_ids))
        if limit is not None:
            statement = statement.limit(limit)

        with self._sessions() as session:
            announcements = session.scalars(statement).all()

        return tuple(
            AnnouncementSummary(
                id=announcement.id,
                course_id=announcement.course_id,
                title=announcement.title,
                body=(
                    announcement.body
                    if announcement.body is not None and announcement.body.strip()
                    else None
                ),
                published_at=_to_utc(announcement.published_at),
            )
            for announcement in announcements
        )

    def get_assignment(self, assignment_id: int) -> AssignmentSummary:
        """Return one assignment (current, or archived with a past course) or not-found."""

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
                item_visible(AssignmentORM, include_past=True),
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
