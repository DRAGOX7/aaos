from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import AssignmentORM, Base, CourseORM
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from aaos.web.academic import AcademicReadService
from aaos.web.contracts import CourseSummary
from aaos.web.errors import Phase7ApiError


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase7-courses.db")
    Base.metadata.create_all(db_engine)

    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def sessions(
    engine: Engine,
) -> sessionmaker[Session]:
    return create_session_factory(engine)


def _store_courses(
    sessions: sessionmaker[Session],
    *courses: CourseORM,
) -> None:
    with sessions() as session:
        session.add_all(courses)
        session.commit()


def test_list_courses_returns_only_active_courses_in_deterministic_order(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=40,
            source_system="moodle",
            source_id="course-40",
            title="Zeta",
            code="AI342",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=30,
            source_system="moodle",
            source_id="course-30",
            title="alpha",
            code="ai342",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=20,
            source_system="moodle",
            source_id="course-20",
            title="Alpha",
            code="AI342",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=10,
            source_system="moodle",
            source_id="course-10",
            title="Big Data",
            code="AI328",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=50,
            source_system="moodle",
            source_id="course-50",
            title="Operating Systems",
            code=None,
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=60,
            source_system="moodle",
            source_id="course-60",
            title="deep learning",
            code=None,
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=1,
            source_system="moodle",
            source_id="course-hidden",
            title="Hidden inactive course",
            code="AA000",
            description=None,
            is_active=False,
        ),
    )

    courses = AcademicReadService(sessions).list_courses()

    assert [course.id for course in courses] == [
        10,
        20,
        30,
        40,
        60,
        50,
    ]


def test_list_courses_returns_immutable_tuple(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=1,
            source_system="moodle",
            source_id="course-1",
            title="Deep Learning",
            code="AI342",
            description=None,
            is_active=True,
        ),
    )

    courses = AcademicReadService(sessions).list_courses()

    assert isinstance(courses, tuple)


def test_list_courses_projects_only_browser_safe_fields(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=7,
            source_system="moodle",
            source_id="internal-moodle-id-7",
            title="Deep Learning",
            code="AI342",
            description="Neural networks and representation learning.",
            is_active=True,
        ),
    )

    courses = AcademicReadService(sessions).list_courses()

    assert len(courses) == 1
    summary = courses[0]

    assert isinstance(summary, CourseSummary)

    rendered = summary.model_dump()

    assert set(rendered) == {
        "id",
        "title",
        "code",
        "description",
        "updated_at",
    }

    assert "source_system" not in rendered
    assert "source_id" not in rendered

    assert summary.id == 7
    assert summary.title == "Deep Learning"
    assert summary.code == "AI342"
    assert summary.description == ("Neural networks and representation learning.")


def test_list_courses_preserves_nullable_code_and_description(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=8,
            source_system="moodle",
            source_id="course-8",
            title="Untitled Moodle Course",
            code=None,
            description=None,
            is_active=True,
        ),
    )

    courses = AcademicReadService(sessions).list_courses()

    assert len(courses) == 1
    assert courses[0].code is None
    assert courses[0].description is None


def test_list_courses_normalizes_updated_at_to_utc(
    sessions: sessionmaker[Session],
) -> None:
    source_time = datetime(
        2026,
        9,
        16,
        15,
        30,
        tzinfo=timezone(timedelta(hours=3)),
    )

    _store_courses(
        sessions,
        CourseORM(
            id=9,
            source_system="moodle",
            source_id="course-9",
            title="Operating Systems",
            code="AI375",
            description=None,
            is_active=True,
            source_updated_at=source_time,
        ),
    )

    courses = AcademicReadService(sessions).list_courses()

    assert len(courses) == 1

    updated_at = courses[0].updated_at

    assert updated_at == datetime(
        2026,
        9,
        16,
        12,
        30,
        tzinfo=timezone.utc,
    )
    assert updated_at is not None
    assert updated_at.utcoffset() == timedelta(0)


def test_list_courses_does_not_mutate_persisted_courses(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=101,
            source_system="moodle",
            source_id="course-101",
            title="Deep Learning",
            code="AI342",
            description="Original description.",
            is_active=True,
        ),
        CourseORM(
            id=102,
            source_system="moodle",
            source_id="course-102",
            title="Inactive Course",
            code="AI999",
            description=None,
            is_active=False,
        ),
    )

    def snapshot() -> tuple[
        tuple[
            int,
            str,
            str,
            str,
            str | None,
            str | None,
            bool,
            datetime | None,
        ],
        ...,
    ]:
        with sessions() as session:
            rows = session.scalars(select(CourseORM).order_by(CourseORM.id)).all()

            return tuple(
                (
                    row.id,
                    row.source_system,
                    row.source_id,
                    row.title,
                    row.code,
                    row.description,
                    row.is_active,
                    row.source_updated_at,
                )
                for row in rows
            )

    before = snapshot()

    AcademicReadService(sessions).list_courses()

    after = snapshot()

    assert after == before


def test_get_course_returns_only_active_course_summary(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=7,
            source_system="moodle",
            source_id="course-7",
            title="Deep Learning",
            code="AI342",
            description="Neural networks.",
            is_active=True,
        ),
    )

    course = AcademicReadService(sessions).get_course(7)

    assert course.id == 7
    assert course.title == "Deep Learning"
    assert course.code == "AI342"
    assert course.description == "Neural networks."


@pytest.mark.parametrize("course_id", [7, 99])
def test_get_course_hides_missing_and_inactive_courses(
    sessions: sessionmaker[Session],
    course_id: int,
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=7,
            source_system="moodle",
            source_id="course-7",
            title="Hidden Course",
            code="AI999",
            description=None,
            is_active=False,
        ),
    )

    with pytest.raises(Phase7ApiError) as raised:
        AcademicReadService(sessions).get_course(course_id)

    assert raised.value.status_code == 404
    assert raised.value.code == "COURSE_NOT_FOUND"


def test_list_assignments_filters_active_courses_and_orders_deterministically(
    sessions: sessionmaker[Session],
) -> None:
    due = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    _store_courses(
        sessions,
        CourseORM(
            id=1,
            source_system="moodle",
            source_id="course-1",
            title="Active",
            code="AI101",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=2,
            source_system="moodle",
            source_id="course-2",
            title="Inactive",
            code="AI102",
            description=None,
            is_active=False,
        ),
    )
    with sessions() as session:
        session.add_all(
            (
                AssignmentORM(
                    id=4,
                    course_id=1,
                    source_system="moodle",
                    source_id="assignment-4",
                    title="No due date",
                    description=None,
                    due_at=None,
                    is_active=True,
                ),
                AssignmentORM(
                    id=2,
                    course_id=1,
                    source_system="moodle",
                    source_id="assignment-2",
                    title="Second tie",
                    description=None,
                    due_at=due,
                    is_active=True,
                ),
                AssignmentORM(
                    id=1,
                    course_id=1,
                    source_system="moodle",
                    source_id="assignment-1",
                    title="First tie",
                    description="Read-only description.",
                    due_at=due,
                    is_active=True,
                ),
                AssignmentORM(
                    id=3,
                    course_id=1,
                    source_system="moodle",
                    source_id="assignment-3",
                    title="Inactive assignment",
                    description=None,
                    due_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    is_active=False,
                ),
                AssignmentORM(
                    id=5,
                    course_id=2,
                    source_system="moodle",
                    source_id="assignment-5",
                    title="Hidden course assignment",
                    description=None,
                    due_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    is_active=True,
                ),
            )
        )
        session.commit()

    assignments = AcademicReadService(sessions).list_assignments()

    assert [assignment.id for assignment in assignments] == [1, 2, 4]
    assert assignments[0].description == "Read-only description."

    filtered = AcademicReadService(sessions).list_assignments(course_id=1)
    assert [assignment.id for assignment in filtered] == [1, 2, 4]


def test_list_assignments_rejects_missing_or_inactive_course_filter(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=2,
            source_system="moodle",
            source_id="course-2",
            title="Inactive",
            code=None,
            description=None,
            is_active=False,
        ),
    )

    for course_id in (2, 404):
        with pytest.raises(Phase7ApiError) as raised:
            AcademicReadService(sessions).list_assignments(course_id)
        assert raised.value.status_code == 404
        assert raised.value.code == "COURSE_NOT_FOUND"
