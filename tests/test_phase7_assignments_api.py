from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.main import create_app
from aaos.persistence.academic_models import AssignmentORM, Base, CourseORM
from aaos.persistence.database import create_database_engine, create_session_factory


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase7-assignments-api.db")
    Base.metadata.create_all(db_engine)
    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


def _client(sessions: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(session_factory=sessions), raise_server_exceptions=False)


def _store_rows(
    sessions: sessionmaker[Session],
    *rows: CourseORM | AssignmentORM,
) -> None:
    with sessions() as session:
        session.add_all(row for row in rows if isinstance(row, CourseORM))
        session.flush()
        session.add_all(row for row in rows if isinstance(row, AssignmentORM))
        session.commit()


def test_course_detail_endpoint_returns_safe_projection(
    sessions: sessionmaker[Session],
) -> None:
    _store_rows(
        sessions,
        CourseORM(
            id=7,
            source_system="private-source",
            source_id="private-course-7",
            title="Deep Learning",
            code="AI342",
            description="Neural networks.",
            is_active=True,
        ),
    )

    response = _client(sessions).get("/api/v1/courses/7")

    assert response.status_code == 200
    assert response.json() == {
        "id": 7,
        "title": "Deep Learning",
        "code": "AI342",
        "description": "Neural networks.",
        "updated_at": None,
    }
    assert "private-source" not in response.text
    assert "private-course-7" not in response.text


@pytest.mark.parametrize("course_id", [2, 404])
def test_course_detail_hides_missing_and_inactive_courses(
    sessions: sessionmaker[Session],
    course_id: int,
) -> None:
    _store_rows(
        sessions,
        CourseORM(
            id=2,
            source_system="moodle",
            source_id="inactive-course",
            title="Hidden Course",
            code="AI999",
            description=None,
            is_active=False,
        ),
    )

    response = _client(sessions).get(f"/api/v1/courses/{course_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"
    assert "Hidden Course" not in response.text
    assert "inactive-course" not in response.text


def test_assignments_endpoint_filters_and_orders_safe_active_rows(
    sessions: sessionmaker[Session],
) -> None:
    due = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    _store_rows(
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
        AssignmentORM(
            id=2,
            course_id=1,
            source_system="moodle",
            source_id="assignment-2",
            title="Second",
            description=None,
            due_at=due,
            is_active=True,
        ),
        AssignmentORM(
            id=1,
            course_id=1,
            source_system="moodle",
            source_id="assignment-1",
            title="First",
            description="Safe description.",
            due_at=due,
            is_active=True,
        ),
        AssignmentORM(
            id=3,
            course_id=1,
            source_system="moodle",
            source_id="inactive-assignment",
            title="Inactive assignment",
            description=None,
            due_at=None,
            is_active=False,
        ),
        AssignmentORM(
            id=4,
            course_id=2,
            source_system="moodle",
            source_id="hidden-assignment",
            title="Hidden assignment",
            description=None,
            due_at=None,
            is_active=True,
        ),
    )

    response = _client(sessions).get("/api/v1/assignments")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["assignments"]] == [1, 2]
    assert response.json()["assignments"][0] == {
        "id": 1,
        "course_id": 1,
        "title": "First",
        "description": "Safe description.",
        "due_at": "2026-09-20T12:00:00Z",
        "updated_at": None,
    }
    assert "hidden-assignment" not in response.text

    filtered = _client(sessions).get("/api/v1/assignments", params={"course_id": 1})
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()["assignments"]] == [1, 2]


@pytest.mark.parametrize("value", ["0", "-1", "not-an-id"])
def test_assignments_rejects_non_positive_or_malformed_course_filter(
    sessions: sessionmaker[Session],
    value: str,
) -> None:
    response = _client(sessions).get(
        "/api/v1/assignments",
        params={"course_id": value},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("course_id", [2, 404])
def test_assignments_missing_or_inactive_course_filter_is_non_leaking(
    sessions: sessionmaker[Session],
    course_id: int,
) -> None:
    _store_rows(
        sessions,
        CourseORM(
            id=2,
            source_system="moodle",
            source_id="inactive-course",
            title="Hidden Course",
            code=None,
            description=None,
            is_active=False,
        ),
    )

    response = _client(sessions).get(
        "/api/v1/assignments",
        params={"course_id": course_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"
    assert "Hidden Course" not in response.text
    assert "inactive-course" not in response.text
