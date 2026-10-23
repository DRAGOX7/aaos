"""Phase 11E: choosing the ChatGPT-plan model is guarded like Gemini, and token-free."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.config import AppSettings
from aaos.inference.selection import ModelSelection, ModelSelectionError
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router

GEMINI_READY = {"GEMINI_API_KEY": "AIzaSyTEST-key", "AAOS_GEMINI_MODEL": "gemini-test"}
PLAN_MODEL = {"AAOS_CHATGPT_PLAN_MODEL": "gpt-test"}
LOCAL_PAGE = {"Host": "localhost:8000", "Origin": "http://localhost:3000"}


def _selection(
    *,
    state: str = "SIGNED_IN",
    environment: dict[str, str] | None = None,
    names: tuple[str, ...] | None = ("gpt-test", "gpt-other"),
) -> ModelSelection:
    async def lister() -> tuple[str, ...]:
        if names is None:
            raise RuntimeError("list unavailable")
        return names

    return ModelSelection(
        AppSettings(),
        {**GEMINI_READY, **PLAN_MODEL} if environment is None else environment,
        chatgpt_plan_lister=lister,
        chatgpt_plan_state=lambda: state,
    )


def _run(coroutine: Awaitable[Any]) -> Any:
    async def scenario() -> Any:
        return await coroutine

    return asyncio.run(scenario())


# --- readiness -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "environment", "blocker"),
    [
        ("NOT_CONFIGURED", PLAN_MODEL, "CHATGPT_PLAN_SIGN_IN_REQUIRED"),
        ("SIGN_IN_REQUIRED", PLAN_MODEL, "CHATGPT_PLAN_SIGN_IN_REQUIRED"),
        ("PLAN_USAGE_NOT_GRANTED", PLAN_MODEL, "CHATGPT_PLAN_USAGE_NOT_GRANTED"),
        ("SIGNED_IN", {}, "CHATGPT_PLAN_MODEL_MISSING"),
        ("SIGNED_IN", PLAN_MODEL, None),
    ],
)
def test_the_plan_is_ready_only_when_signed_in_with_a_model(
    state: str, environment: dict[str, str], blocker: str | None
) -> None:
    status = _selection(state=state, environment=environment).status()
    assert status.chatgpt_plan_blocker == blocker
    assert status.chatgpt_plan_ready is (blocker is None)
    assert status.provider == "lm_studio" and status.cloud is False


def test_without_a_state_callback_the_plan_is_never_ready() -> None:
    status = ModelSelection(AppSettings(), PLAN_MODEL).status()
    assert status.chatgpt_plan_blocker == "CHATGPT_PLAN_SIGN_IN_REQUIRED"


def test_a_failing_state_callback_counts_as_not_signed_in() -> None:
    def broken() -> str:
        raise OSError("disk")

    selection = ModelSelection(AppSettings(), PLAN_MODEL, chatgpt_plan_state=broken)
    assert selection.status().chatgpt_plan_ready is False


# --- switching -----------------------------------------------------------------------------


def test_moving_to_the_plan_needs_confirmation_from_local_and_from_gemini() -> None:
    selection = _selection()
    with pytest.raises(ModelSelectionError) as caught:
        _run(selection.switch("chatgpt_plan"))
    assert caught.value.code == "CLOUD_CONFIRMATION_REQUIRED"

    _run(selection.switch("gemini", confirm_cloud=True))
    with pytest.raises(ModelSelectionError) as caught:
        _run(selection.switch("chatgpt_plan"))  # a different company: confirm again (D5)
    assert caught.value.code == "CLOUD_CONFIRMATION_REQUIRED"

    status = _run(selection.switch("chatgpt_plan", confirm_cloud=True))
    assert (status.provider, status.model, status.cloud) == ("chatgpt_plan", "gpt-test", True)
    with pytest.raises(ModelSelectionError) as caught:
        _run(selection.switch("gemini"))  # and back to Google needs it too
    assert caught.value.code == "CLOUD_CONFIRMATION_REQUIRED"


def test_changing_the_plan_model_within_the_plan_needs_no_new_confirmation() -> None:
    selection = _selection()
    _run(selection.switch("chatgpt_plan", confirm_cloud=True))
    status = _run(selection.switch("chatgpt_plan", chatgpt_plan_model="gpt-other"))
    assert status.model == "gpt-other"
    settings = selection.effective_settings()
    assert settings.model_provider == "chatgpt_plan"
    assert settings.chatgpt_plan_model == "gpt-other" and settings.gemini_model is None


def test_a_plan_that_is_not_ready_or_a_model_it_does_not_offer_is_refused() -> None:
    with pytest.raises(ModelSelectionError) as caught:
        _run(_selection(state="SIGN_IN_REQUIRED").switch("chatgpt_plan", confirm_cloud=True))
    assert caught.value.code == "CHATGPT_PLAN_NOT_READY"
    with pytest.raises(ModelSelectionError) as caught:
        _run(_selection(environment={}).switch("chatgpt_plan", confirm_cloud=True))
    assert caught.value.code == "CHATGPT_PLAN_NOT_READY"
    with pytest.raises(ModelSelectionError) as caught:
        _run(
            _selection().switch(
                "chatgpt_plan", confirm_cloud=True, chatgpt_plan_model="gpt-unknown"
            )
        )
    assert caught.value.code == "INVALID_MODEL"
    with pytest.raises(ModelSelectionError) as caught:
        _run(_selection().switch("gemini", confirm_cloud=True, chatgpt_plan_model="gpt-test"))
    assert caught.value.code == "INVALID_MODEL"


def test_a_switch_is_refused_while_a_question_runs() -> None:
    selection = _selection()

    async def scenario() -> None:
        async with selection.use():
            await selection.switch("chatgpt_plan", confirm_cloud=True)

    with pytest.raises(ModelSelectionError) as caught:
        asyncio.run(scenario())
    assert caught.value.code == "MODEL_BUSY"
    assert selection.status().provider == "lm_studio"


def test_a_failed_rebuild_puts_everything_back() -> None:
    selection = _selection()
    calls: list[str] = []

    async def failing_listener() -> None:
        calls.append(selection.status().provider)
        if len(calls) == 1:
            raise RuntimeError("rebuild failed")

    selection.add_listener(failing_listener)
    with pytest.raises(ModelSelectionError) as caught:
        _run(selection.switch("chatgpt_plan", confirm_cloud=True, chatgpt_plan_model="gpt-other"))
    assert caught.value.code == "MODEL_SWITCH_FAILED"
    status = selection.status()
    assert status.provider == "lm_studio" and status.chatgpt_plan_model == "gpt-test"


def test_the_model_list_is_cached_and_needs_a_sign_in() -> None:
    calls = 0

    async def lister() -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("gpt-test",)

    selection = ModelSelection(
        AppSettings(),
        PLAN_MODEL,
        chatgpt_plan_lister=lister,
        chatgpt_plan_state=lambda: "SIGNED_IN",
    )
    assert _run(selection.chatgpt_plan_models()) == ("gpt-test",)
    assert _run(selection.chatgpt_plan_models()) == ("gpt-test",)
    assert calls == 1
    with pytest.raises(ModelSelectionError) as caught:
        _run(_selection(state="NOT_CONFIGURED").chatgpt_plan_models())
    assert caught.value.code == "CHATGPT_PLAN_NOT_READY"
    with pytest.raises(ModelSelectionError) as caught:
        _run(_selection(names=None).chatgpt_plan_models())
    assert caught.value.code == "MODEL_LIST_FAILED"


# --- routes --------------------------------------------------------------------------------


def _client(selection: ModelSelection) -> TestClient:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(SimpleNamespace(), model_selection=selection)  # type: ignore[arg-type]
    )
    return TestClient(application)


def test_the_routes_list_plan_models_and_switch_with_names_only() -> None:
    client = _client(_selection())
    assert client.get("/api/v1/model/chatgpt-plan-models").json() == {
        "models": ["gpt-test", "gpt-other"]
    }
    body = client.get("/api/v1/model").json()
    assert body["chatgpt_plan_ready"] is True and body["chatgpt_plan_model"] == "gpt-test"

    refused = client.post("/api/v1/model", json={"provider": "chatgpt_plan"}, headers=LOCAL_PAGE)
    assert refused.json()["error"]["code"] == "CLOUD_CONFIRMATION_REQUIRED"
    switched = client.post(
        "/api/v1/model",
        json={"provider": "chatgpt_plan", "confirm_cloud": True, "chatgpt_plan_model": "gpt-other"},
        headers=LOCAL_PAGE,
    )
    assert switched.status_code == 200
    assert (switched.json()["provider"], switched.json()["model"]) == ("chatgpt_plan", "gpt-other")


def test_the_routes_report_a_missing_sign_in_as_a_conflict() -> None:
    client = _client(_selection(state="NOT_CONFIGURED"))
    listed = client.get("/api/v1/model/chatgpt-plan-models")
    assert (listed.status_code, listed.json()["error"]["code"]) == (409, "CHATGPT_PLAN_NOT_READY")
    switched = client.post(
        "/api/v1/model",
        json={"provider": "chatgpt_plan", "confirm_cloud": True},
        headers=LOCAL_PAGE,
    )
    assert (switched.status_code, switched.json()["error"]["code"]) == (
        409,
        "CHATGPT_PLAN_NOT_READY",
    )
    assert "sign-in" in switched.json()["error"]["message"]


def test_the_browser_cannot_send_a_token_or_start_a_sign_in() -> None:
    client = _client(_selection())
    sneaky = client.post(
        "/api/v1/model",
        json={"provider": "chatgpt_plan", "confirm_cloud": True, "access_token": "x"},
        headers=LOCAL_PAGE,
    )
    assert sneaky.status_code == 422
    paths = set(client.app.openapi()["paths"])  # type: ignore[attr-defined]
    assert "/api/v1/model/chatgpt-plan-models" in paths
    assert not any("sign-in" in path or "signin" in path or "oauth" in path for path in paths)
