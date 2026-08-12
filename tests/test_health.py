"""Contract tests for the AAOS process health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient
from httpx2 import Response

from aaos.main import app


def get_health_response() -> Response:
    """Request the health endpoint through the real ASGI application."""
    with TestClient(app) as client:
        return client.get("/health")


def test_health_returns_http_200() -> None:
    """Catch a missing or failing health route."""
    response = get_health_response()

    assert response.status_code == 200


def test_health_returns_stable_service_metadata() -> None:
    """Catch incomplete or incorrect process-health metadata."""
    response = get_health_response()

    assert response.json() == {
        "status": "ok",
        "service": "aaos",
        "version": "0.1.0",
    }


def test_health_returns_json_content_type() -> None:
    """Catch a route that returns the contract in a non-JSON representation."""
    response = get_health_response()

    assert response.headers["content-type"] == "application/json"
