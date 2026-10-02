"""Which courses and course items reads may show: current, and past when named.

A course is past (archived) when a complete sync stopped listing it; its items
that disappeared in the same sync archived with it. An item removed while its
course stayed current is never shown. See the archived-courses spec.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_

from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)


def course_visible(*, include_past: bool) -> ColumnElement[bool]:
    """Current courses, plus past (archived) ones when ``include_past``."""
    current = CourseORM.is_active.is_(True)
    if not include_past:
        return current
    return or_(current, and_(CourseORM.is_active.is_(False), CourseORM.archived_at.is_not(None)))


def item_visible(
    model: type[AssignmentORM] | type[AnnouncementORM] | type[RemoteFileORM],
    *,
    include_past: bool,
) -> ColumnElement[bool]:
    """Items of current courses, plus items archived with a past course.

    The query must join ``CourseORM`` on the item's course.
    """
    current = and_(model.is_active.is_(True), CourseORM.is_active.is_(True))
    if not include_past:
        return current
    past = and_(
        CourseORM.is_active.is_(False),
        CourseORM.archived_at.is_not(None),
        model.archived_at.is_not(None),
    )
    return or_(current, past)
