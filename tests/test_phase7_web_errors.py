from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.errors import Phase7ApiError, install_error_boundary


def _app() -> FastAPI:
    app = FastAPI()
    install_error_boundary(app)

    @app.get("/expected")
    async def expected() -> None:
        raise Phase7ApiError(
            503,
            "SYSTEM_UNAVAILABLE",
            "AAOS is temporarily unavailable.",
        )

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError(
            r"secret path C:\Users\example\data\credentials\token.json"
        )

    @app.get("/validated/{item_id}")
    async def validated(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


def test_expected_api_error_has_closed_safe_shape_and_request_id() -> None:
    response = TestClient(
        _app(),
        raise_server_exceptions=False,
    ).get("/expected")

    assert response.status_code == 503

    payload = response.json()

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {
        "code",
        "message",
        "request_id",
    }

    assert payload["error"]["code"] == "SYSTEM_UNAVAILABLE"
    assert payload["error"]["message"] == "AAOS is temporarily unavailable."

    request_id = payload["error"]["request_id"]

    assert len(request_id) == 32
    assert request_id == request_id.lower()
    assert all(character in "0123456789abcdef" for character in request_id)

    assert response.headers["X-Request-ID"] == request_id


def test_unexpected_exception_never_leaks_exception_text_or_local_path() -> None:
    response = TestClient(
        _app(),
        raise_server_exceptions=False,
    ).get("/unexpected")

    assert response.status_code == 500

    rendered = response.text

    assert "RuntimeError" not in rendered
    assert "Users" not in rendered
    assert "credentials" not in rendered
    assert "token.json" not in rendered

    payload = response.json()

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {
        "code",
        "message",
        "request_id",
    }

    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "AAOS could not complete the request."

    assert response.headers["X-Request-ID"] == payload["error"]["request_id"]


def test_validation_error_uses_safe_closed_error_contract() -> None:
    response = TestClient(
        _app(),
        raise_server_exceptions=False,
    ).get("/validated/not-an-integer")

    assert response.status_code == 422

    payload = response.json()

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {
        "code",
        "message",
        "request_id",
    }

    assert payload["error"]["code"] == "INVALID_REQUEST"
    assert payload["error"]["message"] == "The request is invalid."

    rendered = response.text

    assert "int_parsing" not in rendered
    assert "type_error" not in rendered
    assert "input" not in rendered

    assert response.headers["X-Request-ID"] == payload["error"]["request_id"]
