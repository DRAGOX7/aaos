"""RED tests for browser-safe approval read routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aaos.main import create_app
from aaos.persistence import approval_models, task_models  # noqa: F401
from aaos.persistence.academic_models import Base
from aaos.persistence.database import create_database_engine, create_session_factory


def test_empty_approval_list_is_a_closed_browser_response(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "phase7e-approval-read.db")
    Base.metadata.create_all(engine)
    try:
        client = TestClient(
            create_app(session_factory=create_session_factory(engine)),
            raise_server_exceptions=False,
        )
        response = client.get("/api/v1/approvals")
        assert response.status_code == 200
        assert response.json() == {"approvals": []}
    finally:
        engine.dispose()


def test_missing_approval_detail_is_a_stable_not_found_error(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "phase7e-approval-detail.db")
    Base.metadata.create_all(engine)
    try:
        client = TestClient(
            create_app(session_factory=create_session_factory(engine)),
            raise_server_exceptions=False,
        )
        response = client.get("/api/v1/approvals/missing-approval")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
    finally:
        engine.dispose()
