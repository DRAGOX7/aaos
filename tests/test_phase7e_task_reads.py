"""RED tests for browser-safe task read routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.main import create_app
from aaos.persistence import approval_models, task_models  # noqa: F401
from aaos.persistence.academic_models import Base
from aaos.persistence.database import create_database_engine, create_session_factory


def _client(sessions: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(session_factory=sessions), raise_server_exceptions=False)


def test_empty_task_list_is_a_closed_browser_response(tmp_path: Path) -> None:
    engine: Engine = create_database_engine(tmp_path / "phase7e-task-read.db")
    Base.metadata.create_all(engine)
    try:
        response = _client(create_session_factory(engine)).get("/api/v1/tasks")
        assert response.status_code == 200
        assert response.json() == {"tasks": []}
    finally:
        engine.dispose()


def test_missing_task_detail_is_a_stable_not_found_error(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "phase7e-task-detail.db")
    Base.metadata.create_all(engine)
    try:
        response = _client(create_session_factory(engine)).get("/api/v1/tasks/missing-task")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
    finally:
        engine.dispose()
