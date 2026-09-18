from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.errors import install_error_boundary
from aaos.web.health import (
    HealthComponent,
    HealthComponentName,
    HealthDetailCode,
    HealthStatus,
    SystemHealthResponse,
)
from aaos.web.routes import create_api_v1_router


class EmptyCourseReader:
    def list_courses(self):
        return ()


class FakeHealthReader:
    def check(self, *, now):
        assert now.tzinfo is not None
        return SystemHealthResponse(
            schema_version="phase7-health-v1",
            checked_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
            overall=HealthStatus.HEALTHY,
            components=(
                HealthComponent(
                    name=HealthComponentName.DATABASE,
                    status=HealthStatus.HEALTHY,
                    required=True,
                    detail_code=HealthDetailCode.RAG_INDEX_READY,
                ),
            ),
        )


def test_system_health_route_is_get_only_and_returns_closed_projection() -> None:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(
            EmptyCourseReader(),
            system_health_reader=FakeHealthReader(),
        )
    )
    client = TestClient(application)

    response = client.get("/api/v1/system-health")

    assert response.status_code == 200
    assert response.json()["schema_version"] == "phase7-health-v1"
    assert response.json()["overall"] == "HEALTHY"
    assert client.post("/api/v1/system-health").status_code == 405
