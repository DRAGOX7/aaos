from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.activity import ActivityCursor, encode_activity_cursor
from aaos.web.activity_read import ActivityReadService, ActivityReplayUnavailable
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


class RecoveryActivityReader:
    def __init__(self) -> None:
        self.after = None

    def list_history(self, *, limit, before):
        del limit, before
        raise AssertionError("history is not used by the stream test")

    def tail(self, *, after, limit):
        del after, limit
        return ()

    def tail_with_cursors(self, *, after, limit):
        self.after = after
        assert limit == 50
        raise ActivityReplayUnavailable

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


def test_activity_stream_has_sse_headers_and_bounded_recovery() -> None:
    application = FastAPI()
    install_error_boundary(application)
    reader = RecoveryActivityReader()
    application.include_router(create_api_v1_router(EmptyCourseReader(), activity_reader=reader))
    client = TestClient(application)
    expected_cursor = ActivityCursor(datetime(2026, 9, 18, tzinfo=timezone.utc), 1, "event-1", 1)
    cursor = encode_activity_cursor(expected_cursor)

    response = client.get(
        f"/api/v1/activity/stream?after={cursor}",
        headers={"Last-Event-ID": "invalid-but-overridden"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == 'event: recovery_required\ndata: {"reason":"REPLAY_REQUIRED"}\n\n'
    assert reader.after == expected_cursor


def test_activity_stream_rejects_malformed_cursor() -> None:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(EmptyCourseReader(), activity_reader=RecoveryActivityReader())
    )
    client = TestClient(application)

    response = client.get("/api/v1/activity/stream?after=not-a-cursor")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
