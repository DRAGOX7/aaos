from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.main import create_app
from aaos.persistence.academic_models import Base, CourseORM
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase7-api.db")
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


def _client(
    sessions: sessionmaker[Session],
) -> TestClient:
    return TestClient(
        create_app(session_factory=sessions),
        raise_server_exceptions=False,
    )


def _store_courses(
    sessions: sessionmaker[Session],
    *courses: CourseORM,
) -> None:
    with sessions() as session:
        session.add_all(courses)
        session.commit()


def test_courses_endpoint_returns_exact_safe_contract(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=7,
            source_system="moodle",
            source_id="internal-course-7",
            title="Deep Learning",
            code="AI342",
            description="Neural networks.",
            is_active=True,
        ),
    )

    response = _client(sessions).get("/api/v1/courses")

    assert response.status_code == 200

    payload = response.json()

    assert set(payload) == {"courses"}
    assert len(payload["courses"]) == 1

    course = payload["courses"][0]

    assert set(course) == {
        "id",
        "title",
        "code",
        "description",
        "updated_at",
    }

    assert course == {
        "id": 7,
        "title": "Deep Learning",
        "code": "AI342",
        "description": "Neural networks.",
        "updated_at": None,
    }


def test_courses_endpoint_excludes_inactive_courses(
    sessions: sessionmaker[Session],
) -> None:
    _store_courses(
        sessions,
        CourseORM(
            id=1,
            source_system="moodle",
            source_id="active",
            title="Big Data",
            code="AI328",
            description=None,
            is_active=True,
        ),
        CourseORM(
            id=2,
            source_system="moodle",
            source_id="inactive",
            title="Hidden Course",
            code="AI999",
            description=None,
            is_active=False,
        ),
    )

    response = _client(sessions).get("/api/v1/courses")

    assert response.status_code == 200
    assert [course["id"] for course in response.json()["courses"]] == [1]


def test_courses_endpoint_has_deterministic_order(
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
    )

    response = _client(sessions).get("/api/v1/courses")

    assert response.status_code == 200
    assert [course["id"] for course in response.json()["courses"]] == [
        10,
        20,
        30,
        40,
        60,
        50,
    ]


def test_courses_endpoint_preserves_existing_health_contract(
    sessions: sessionmaker[Session],
) -> None:
    response = _client(sessions).get("/health")

    assert response.status_code == 200

    payload = response.json()

    assert set(payload) == {
        "status",
        "service",
        "version",
    }
    assert payload["status"] == "ok"


def test_courses_endpoint_does_not_expose_source_identity_anywhere(
    sessions: sessionmaker[Session],
) -> None:
    secret_source_system = "moodle-secret-source"
    secret_source_id = "internal-secret-course-id"

    _store_courses(
        sessions,
        CourseORM(
            id=1,
            source_system=secret_source_system,
            source_id=secret_source_id,
            title="Deep Learning",
            code="AI342",
            description=None,
            is_active=True,
        ),
    )

    response = _client(sessions).get("/api/v1/courses")

    assert response.status_code == 200

    rendered = response.text

    assert secret_source_system not in rendered
    assert secret_source_id not in rendered
    assert "source_system" not in rendered
    assert "source_id" not in rendered


def test_courses_route_layer_contains_no_direct_sql_query() -> None:
    route_source = Path("src/aaos/web/routes.py").read_text(
        encoding="utf-8"
    )

    forbidden_tokens = (
        "CourseORM",
        "select(",
        "Session.execute",
    )

    for token in forbidden_tokens:
        assert token not in route_source
