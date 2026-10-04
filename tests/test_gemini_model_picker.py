"""Choosing the Gemini model from a list, without restarting the backend."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.config import AppSettings
from aaos.inference.gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceProtocolError,
)
from aaos.inference.gemini import chat_model_names, fetch_gemini_model_names
from aaos.inference.selection import ModelSelection, ModelSelectionError
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router

KEY = "AIzaSyTEST-not-a-real-key-0123456789"
READY = {"GEMINI_API_KEY": KEY, "AAOS_GEMINI_MODEL": "gemini-3.7-flash"}
LISTED = ("gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.8-flash")

# A trimmed copy of the model list a real key returns.
REAL_LIST = (
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-preview-tts",
    "models/gemma-4-31b-it",
    "models/gemini-flash-latest",
    "models/gemini-2.5-flash-image",
    "models/gemini-3.8-flash",
    "models/gemini-3.5-flash-lite",
    "models/lyria-3.5",
    "models/gemini-3.8-flash-tts",
    "models/gemini-embedding-001",
    "models/gemini-3.5-transcribe-live",
    "models/gemini-3.8-live",
    "models/veo-3.1-generate-preview",
    "models/aqa",
    "models/deep-research-max-preview-04-2026",
    "models/nano-banana-pro-preview",
    "models/gemini-robotics-er-2-preview",
    "models/gemini-3.1-flash-lite",
)


# --- the list ------------------------------------------------------------------------------


def test_only_chat_models_are_offered_and_the_prefix_is_dropped() -> None:
    assert chat_model_names(REAL_LIST) == (
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemma-4-31b-it",
    )


def _list_transport(handler):  # type: ignore[no-untyped-def]
    return httpx2.MockTransport(handler)


def test_the_model_list_is_fetched_from_the_fixed_endpoint_with_the_key() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"data": [{"id": name} for name in REAL_LIST]})

    names = asyncio.run(fetch_gemini_model_names(KEY, _list_transport(handler)))

    assert "gemini-3.8-flash" in names and "gemini-3.8-flash-tts" not in names
    (request,) = seen
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.url.path == "/v1beta/openai/models"
    assert request.headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    ("handler", "error"),
    (
        (
            lambda r: httpx2.Response(403, json={"error": {"message": f"bad {KEY}"}}),
            InferenceBackendError,
        ),
        (lambda r: httpx2.Response(200, json={"unexpected": 1}), InferenceProtocolError),
        (lambda r: httpx2.Response(200, text="not json"), InferenceProtocolError),
    ),
)
def test_a_failed_model_list_is_a_typed_error_that_never_shows_the_key(handler, error) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(error) as caught:
        asyncio.run(fetch_gemini_model_names(KEY, _list_transport(handler)))

    assert KEY not in str(caught.value)


def test_an_unreachable_endpoint_is_a_connection_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {KEY}", request=request)

    with pytest.raises(InferenceConnectionError) as caught:
        asyncio.run(fetch_gemini_model_names(KEY, _list_transport(handler)))

    assert KEY not in str(caught.value)


# --- choosing a model --------------------------------------------------------------------


def _selection(
    environment: dict[str, str] | None = None,
    names: tuple[str, ...] | None = LISTED,
    calls: list[int] | None = None,
) -> ModelSelection:
    async def lister() -> tuple[str, ...]:
        if calls is not None:
            calls.append(1)
        if names is None:
            raise RuntimeError("Google is down")
        return names

    return ModelSelection(AppSettings(), READY if environment is None else environment, lister)


def test_the_list_is_fetched_on_demand_and_kept_for_a_while() -> None:
    async def scenario() -> None:
        calls: list[int] = []
        selection = _selection(calls=calls)
        assert await selection.gemini_models() == LISTED
        assert await selection.gemini_models() == LISTED
        assert len(calls) == 1

    asyncio.run(scenario())


def test_the_list_needs_a_key_and_reports_a_failed_fetch_in_safe_words() -> None:
    async def scenario() -> None:
        with pytest.raises(ModelSelectionError) as no_key:
            await _selection({}).gemini_models()
        assert no_key.value.code == "GEMINI_NOT_READY"

        with pytest.raises(ModelSelectionError) as down:
            await _selection(names=None).gemini_models()
        assert down.value.code == "MODEL_LIST_FAILED"

    asyncio.run(scenario())


def test_a_listed_model_can_be_chosen_when_entering_the_cloud() -> None:
    async def scenario() -> None:
        selection = _selection()
        with pytest.raises(ModelSelectionError) as unconfirmed:
            await selection.switch("gemini", gemini_model="gemini-3.8-flash")
        assert unconfirmed.value.code == "CLOUD_CONFIRMATION_REQUIRED"

        status = await selection.switch(
            "gemini", confirm_cloud=True, gemini_model="models/gemini-3.8-flash"
        )

        assert (status.provider, status.model) == ("gemini", "gemini-3.8-flash")
        assert selection.effective_settings().gemini_model == "gemini-3.8-flash"

    asyncio.run(scenario())


def test_changing_the_model_while_in_the_cloud_needs_no_new_confirmation_and_rebuilds() -> None:
    async def scenario() -> None:
        resets: list[int] = []

        async def listener() -> None:
            resets.append(1)

        selection = _selection()
        await selection.switch("gemini", confirm_cloud=True)
        selection.add_listener(listener)

        status = await selection.switch("gemini", gemini_model="gemini-3.1-flash-lite")

        assert status.model == "gemini-3.1-flash-lite"
        assert resets == [1]  # the assistant and the document runtime were rebuilt
        await selection.switch("gemini", gemini_model="gemini-3.1-flash-lite")
        assert resets == [1]  # choosing the current model again does nothing

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ("../etc/passwd", "a b", "x" * 101, "", "model;drop", "ünï"))
def test_a_model_name_that_is_not_a_plain_identifier_is_refused(name: str) -> None:
    async def scenario() -> None:
        with pytest.raises(ModelSelectionError) as refused:
            await _selection().switch("gemini", confirm_cloud=True, gemini_model=name)
        assert refused.value.code == "INVALID_MODEL"

    asyncio.run(scenario())


def test_a_model_google_does_not_list_for_this_key_is_refused() -> None:
    async def scenario() -> None:
        selection = _selection()
        with pytest.raises(ModelSelectionError) as refused:
            await selection.switch("gemini", confirm_cloud=True, gemini_model="gemini-9-ultra")
        assert refused.value.code == "INVALID_MODEL"
        assert selection.status().provider == "lm_studio"

    asyncio.run(scenario())


def test_if_the_list_cannot_be_read_a_well_formed_name_is_still_allowed() -> None:
    async def scenario() -> None:
        status = await _selection(names=None).switch(
            "gemini", confirm_cloud=True, gemini_model="gemini-3.8-flash"
        )
        assert status.model == "gemini-3.8-flash"

    asyncio.run(scenario())


def test_a_model_cannot_be_named_for_the_local_provider() -> None:
    async def scenario() -> None:
        with pytest.raises(ModelSelectionError) as refused:
            await _selection().switch("lm_studio", gemini_model="gemini-3.8-flash")
        assert refused.value.code == "INVALID_MODEL"

    asyncio.run(scenario())


def test_a_model_change_is_refused_while_a_question_runs_and_rolls_back_on_failure() -> None:
    async def scenario() -> None:
        selection = _selection()
        await selection.switch("gemini", confirm_cloud=True)
        async with selection.use():
            with pytest.raises(ModelSelectionError) as busy:
                await selection.switch("gemini", gemini_model="gemini-3.1-flash-lite")
            assert busy.value.code == "MODEL_BUSY"
        assert selection.status().model == "gemini-3.7-flash"

        attempts = 0

        async def flaky() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("rebuild failed")

        selection.add_listener(flaky)
        with pytest.raises(ModelSelectionError) as failed:
            await selection.switch("gemini", gemini_model="gemini-3.8-flash")
        assert failed.value.code == "MODEL_SWITCH_FAILED"
        assert selection.status().model == "gemini-3.7-flash"  # the old model is back

    asyncio.run(scenario())


# --- the API ---------------------------------------------------------------------------

LOCAL_PAGE = {"Host": "localhost:8000", "Origin": "http://localhost:3000"}


def _client(selection: ModelSelection) -> TestClient:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(SimpleNamespace(), model_selection=selection)  # type: ignore[arg-type]
    )
    return TestClient(application)


def test_the_list_endpoint_returns_names_only() -> None:
    body = _client(_selection()).get("/api/v1/model/gemini-models").json()

    assert body == {"models": list(LISTED)}
    assert KEY not in json.dumps(body)


def test_the_list_endpoint_explains_a_missing_key_and_a_failed_fetch() -> None:
    missing = _client(_selection({})).get("/api/v1/model/gemini-models")
    assert (missing.status_code, missing.json()["error"]["code"]) == (409, "GEMINI_NOT_READY")

    down = _client(_selection(names=None)).get("/api/v1/model/gemini-models")
    assert (down.status_code, down.json()["error"]["code"]) == (503, "MODEL_LIST_FAILED")


def test_choosing_a_model_over_the_api_works_from_the_local_page_only() -> None:
    client = _client(_selection())
    body = {"provider": "gemini", "confirm_cloud": True, "gemini_model": "gemini-3.8-flash"}

    foreign = client.post(
        "/api/v1/model", json=body, headers={**LOCAL_PAGE, "Origin": "https://x.example"}
    )
    assert foreign.status_code == 403

    chosen = client.post("/api/v1/model", json=body, headers=LOCAL_PAGE)
    assert chosen.status_code == 200
    assert (chosen.json()["provider"], chosen.json()["model"]) == ("gemini", "gemini-3.8-flash")


def test_an_unlisted_or_malformed_model_is_refused_over_the_api() -> None:
    client = _client(_selection())

    for name in ("gemini-9-ultra", "../x"):
        response = client.post(
            "/api/v1/model",
            json={"provider": "gemini", "confirm_cloud": True, "gemini_model": name},
            headers=LOCAL_PAGE,
        )
        assert (response.status_code, response.json()["error"]["code"]) == (422, "INVALID_MODEL")
