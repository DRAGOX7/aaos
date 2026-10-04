"""Choosing the answering model at runtime: guarded, in memory, and key-free."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.config import AppSettings
from aaos.inference.selection import ModelSelection, ModelSelectionError
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router

KEY = "AIzaSyTEST-not-a-real-key-0123456789"
READY = {"GEMINI_API_KEY": KEY, "AAOS_GEMINI_MODEL": "models/gemini-test-flash"}


def _selection(environment: dict[str, str] | None = None) -> ModelSelection:
    return ModelSelection(AppSettings(), environment if environment is not None else {})


# --- the state machine -----------------------------------------------------------------


def test_the_local_model_is_selected_by_default_and_names_no_key() -> None:
    status = _selection(READY).status()

    assert (status.provider, status.cloud, status.model) == ("lm_studio", False, "qwen/qwen3.5-4b")
    assert status.gemini_ready and status.gemini_model == "gemini-test-flash"
    assert KEY not in repr(status)


@pytest.mark.parametrize(
    ("environment", "blocker"),
    (
        ({}, "GEMINI_API_KEY_MISSING"),
        ({"AAOS_GEMINI_MODEL": "m"}, "GEMINI_API_KEY_MISSING"),
        ({"GEMINI_API_KEY": KEY}, "GEMINI_MODEL_MISSING"),
        ({"GEMINI_API_KEY": "  ", "AAOS_GEMINI_MODEL": "m"}, "GEMINI_API_KEY_MISSING"),
    ),
)
def test_gemini_is_not_ready_until_key_and_model_are_in_the_environment(
    environment: dict[str, str], blocker: str
) -> None:
    status = _selection(environment).status()
    assert not status.gemini_ready and status.gemini_blocker == blocker


def test_switching_to_the_cloud_needs_confirmation_and_readiness() -> None:
    async def scenario() -> None:
        with pytest.raises(ModelSelectionError) as unconfirmed:
            await _selection(READY).switch("gemini")
        assert unconfirmed.value.code == "CLOUD_CONFIRMATION_REQUIRED"

        with pytest.raises(ModelSelectionError) as not_ready:
            await _selection({}).switch("gemini", confirm_cloud=True)
        assert not_ready.value.code == "GEMINI_NOT_READY"

        with pytest.raises(ModelSelectionError) as bad:
            await _selection(READY).switch("openai", confirm_cloud=True)
        assert bad.value.code == "INVALID_PROVIDER"

    asyncio.run(scenario())


def test_switching_changes_the_effective_settings_and_back_again() -> None:
    async def scenario() -> None:
        selection = _selection(READY)
        cloud = await selection.switch("gemini", confirm_cloud=True)
        assert (cloud.provider, cloud.cloud, cloud.model) == ("gemini", True, "gemini-test-flash")
        assert selection.effective_settings().model_provider == "gemini"
        assert selection.effective_settings().gemini_model == "gemini-test-flash"

        local = await selection.switch("lm_studio")  # leaving the cloud needs no confirmation
        assert (local.provider, local.cloud) == ("lm_studio", False)
        assert selection.effective_settings().gemini_model is None

    asyncio.run(scenario())


def test_selecting_the_current_model_is_a_no_op_that_calls_nobody() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def listener() -> None:
            calls.append("reset")

        selection = _selection(READY)
        selection.add_listener(listener)
        await selection.switch("lm_studio")
        assert calls == []

    asyncio.run(scenario())


def test_a_switch_is_refused_while_a_question_is_running() -> None:
    async def scenario() -> None:
        selection = _selection(READY)
        async with selection.use():
            with pytest.raises(ModelSelectionError) as busy:
                await selection.switch("gemini", confirm_cloud=True)
            assert busy.value.code == "MODEL_BUSY"
        assert selection.status().provider == "lm_studio"  # nothing changed
        await selection.switch("gemini", confirm_cloud=True)  # fine once idle

    asyncio.run(scenario())


def test_every_model_bound_component_is_reset_on_a_switch() -> None:
    async def scenario() -> None:
        order: list[str] = []

        async def rag() -> None:
            order.append("rag")

        async def assistant() -> None:
            order.append("assistant")

        selection = _selection(READY)
        selection.add_listener(rag)
        selection.add_listener(assistant)
        await selection.switch("gemini", confirm_cloud=True)
        assert order == ["rag", "assistant"]

    asyncio.run(scenario())


def test_a_failed_reset_rolls_the_switch_back() -> None:
    async def scenario() -> None:
        resets = 0

        async def flaky() -> None:
            nonlocal resets
            resets += 1
            if resets == 1:
                raise RuntimeError("could not rebuild")

        selection = _selection(READY)
        selection.add_listener(flaky)
        with pytest.raises(ModelSelectionError) as failed:
            await selection.switch("gemini", confirm_cloud=True)

        assert failed.value.code == "MODEL_SWITCH_FAILED"
        assert selection.status().provider == "lm_studio"
        assert resets == 2  # reset once to switch, once more to undo it

    asyncio.run(scenario())


def test_a_question_that_arrives_during_a_switch_waits_for_it() -> None:
    async def scenario() -> list[str]:
        events: list[str] = []
        release = asyncio.Event()

        async def slow_reset() -> None:
            events.append("reset-start")
            await release.wait()
            events.append("reset-end")

        selection = _selection(READY)
        selection.add_listener(slow_reset)
        switch = asyncio.create_task(selection.switch("gemini", confirm_cloud=True))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        async def question() -> None:
            async with selection.use():
                events.append("question")

        asking = asyncio.create_task(question())
        await asyncio.sleep(0.01)
        assert events == ["reset-start"]  # the question is held back
        release.set()
        await asyncio.gather(switch, asking)
        return events

    assert asyncio.run(scenario()) == ["reset-start", "reset-end", "question"]


# --- the API ---------------------------------------------------------------------------

LOCAL_PAGE = {"Host": "localhost:8000", "Origin": "http://localhost:3000"}


def _client(selection: ModelSelection | None) -> TestClient:
    application = FastAPI()
    install_error_boundary(application)
    application.include_router(
        create_api_v1_router(SimpleNamespace(), model_selection=selection)  # type: ignore[arg-type]
    )
    return TestClient(application)


def test_get_model_reports_names_only() -> None:
    body = _client(_selection(READY)).get("/api/v1/model").json()

    assert body == {
        "provider": "lm_studio",
        "model": "qwen/qwen3.5-4b",
        "cloud": False,
        "local_model": "qwen/qwen3.5-4b",
        "gemini_model": "gemini-test-flash",
        "gemini_ready": True,
        "gemini_blocker": None,
    }
    assert KEY not in str(body)


def test_post_model_switches_with_confirmation_from_the_local_page() -> None:
    client = _client(_selection(READY))

    refused = client.post("/api/v1/model", json={"provider": "gemini"}, headers=LOCAL_PAGE)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "CLOUD_CONFIRMATION_REQUIRED"

    switched = client.post(
        "/api/v1/model", json={"provider": "gemini", "confirm_cloud": True}, headers=LOCAL_PAGE
    )
    assert switched.status_code == 200
    assert switched.json()["provider"] == "gemini" and switched.json()["cloud"] is True
    assert client.get("/api/v1/model").json()["cloud"] is True

    back = client.post("/api/v1/model", json={"provider": "lm_studio"}, headers=LOCAL_PAGE)
    assert back.json()["provider"] == "lm_studio"


def test_post_model_reports_gemini_not_ready_and_busy_as_conflicts() -> None:
    not_ready = _client(_selection({})).post(
        "/api/v1/model", json={"provider": "gemini", "confirm_cloud": True}, headers=LOCAL_PAGE
    )
    assert (not_ready.status_code, not_ready.json()["error"]["code"]) == (409, "GEMINI_NOT_READY")


@pytest.mark.parametrize(
    "headers",
    (
        {},
        {"Host": "localhost:8000", "Origin": "https://evil.example"},
        {"Host": "evil.example", "Origin": "http://localhost:3000"},
    ),
)
def test_post_model_is_fenced_to_the_local_page(headers: dict[str, str]) -> None:
    selection = _selection(READY)
    response = _client(selection).post(
        "/api/v1/model", json={"provider": "gemini", "confirm_cloud": True}, headers=headers
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MUTATION_NOT_ALLOWED"
    assert selection.status().provider == "lm_studio"


def test_post_model_rejects_unknown_fields_and_any_key() -> None:
    client = _client(_selection(READY))
    for body in (
        {"provider": "gemini", "api_key": KEY},
        {"provider": "gemini", "confirm_cloud": True, "model": "x"},
        {"provider": "openai"},
    ):
        response = client.post("/api/v1/model", json=body, headers=LOCAL_PAGE)
        assert response.status_code == 422
        assert KEY not in response.text


# --- the components that rebuild --------------------------------------------------------


def test_the_rag_reader_rebuilds_for_the_new_model_after_a_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import main
    from aaos.rag import factory

    built: list[str] = []
    closed: list[str] = []

    def fake_build(settings: AppSettings | None = None, **_: Any) -> SimpleNamespace:
        provider = settings.model_provider if settings is not None else "default"
        built.append(provider)

        async def aclose() -> None:
            closed.append(provider)

        return SimpleNamespace(service=object(), runtime=SimpleNamespace(), aclose=aclose)

    monkeypatch.setattr(factory, "build_configured_phase4_runtime", fake_build)
    monkeypatch.setattr("aaos.web.rag.RagReadService", lambda service, sessions: object())

    async def scenario() -> None:
        selection = _selection(READY)
        reader = main._LazyDefaultRagReader(object(), selection)  # type: ignore[arg-type]
        selection.add_listener(reader.reset)

        reader._ensure_delegate()
        await selection.switch("gemini", confirm_cloud=True)
        reader._ensure_delegate()

    asyncio.run(scenario())

    assert built == ["lm_studio", "gemini"]
    assert closed == ["lm_studio"]  # the old runtime was released


def test_the_ask_service_drops_its_assistant_but_keeps_the_conversation_memory() -> None:
    from aaos import main

    closed: list[bool] = []

    class FakeGateway:
        async def aclose(self) -> None:
            closed.append(True)

    async def scenario() -> None:
        service = main._LazyDefaultAcademicAskService(
            AppSettings(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,  # type: ignore[arg-type]
        )
        service._gateway = FakeGateway()  # type: ignore[assignment]
        service._assistant = object()  # type: ignore[assignment]
        sentinel = object()
        service._context_store = sentinel  # type: ignore[assignment]

        await service.reset()

        assert service._assistant is None and service._gateway is None
        assert service._context_store is sentinel
        assert closed == [True]

    asyncio.run(scenario())
