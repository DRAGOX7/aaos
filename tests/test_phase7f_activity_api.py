from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.activity_read import ActivityReadService
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router


class EmptyCourseReader:
    def list_courses(self):
        return ()


class EmptyActivityRepository:
    def list_source_rows(self, *, before, after, limit):
        del before, after, limit
        return ()

    def current_high_watermark(self):
        return None


def test_activity_history_route_is_bounded_and_read_only() -> None:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(
            EmptyCourseReader(),
            activity_reader=ActivityReadService(EmptyActivityRepository()),
        )
    )
    client = TestClient(application)

    response = client.get("/api/v1/activity?limit=0")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert client.post("/api/v1/activity").status_code == 405
