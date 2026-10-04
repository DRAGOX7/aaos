"""The optional Gemini model: off by default, narrow when on, and the key never leaks."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from types import SimpleNamespace

import httpx2
import pytest

from aaos.config import AppSettings, settings_from_environment
from aaos.inference.factory import (
    NoOpModelLifecycle,
    active_model_identifier,
    build_model_gateway,
    warn_if_cloud_model,
)
from aaos.inference.gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelUnavailableError,
)
from aaos.inference.gemini import GEMINI_BASE_URL, GeminiGateway, gemini_response_format
from aaos.inference.lm_studio import _STRUCTURED_RESPONSE_FORMATS, LMStudioGateway
from aaos.inference.models import ChatMessage, ChatRequest
from aaos.inference.telemetry import LocalInferenceTelemetryEvent

KEY = "AIzaSyTEST-not-a-real-key-0123456789"
SETTINGS = AppSettings(model_provider="gemini", gemini_model="models/gemini-test-flash")


def _gateway(
    handler: Callable[[httpx2.Request], httpx2.Response],
    events: list[LocalInferenceTelemetryEvent] | None = None,
    *,
    on_failure: Callable[[str], None] | None = None,
    on_success: Callable[[], None] | None = None,
) -> GeminiGateway:
    return GeminiGateway(
        SETTINGS,
        httpx2.MockTransport(handler),
        api_key=KEY,
        diagnostic_sink=events.append if events is not None else None,
        overload_retry_delays=(0.0, 0.0),
        on_failure=on_failure,
        on_success=on_success,
    )


def _ok(content: str = "Hello") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "model": "gemini-test-flash",
            "choices": [{"message": {"role": "assistant", "content": content}}],
        },
    )


def _run(gateway: GeminiGateway, request: ChatRequest):  # type: ignore[no-untyped-def]
    async def scenario():  # type: ignore[no-untyped-def]
        try:
            return await gateway.generate(request)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


REQUEST = ChatRequest(messages=(ChatMessage("user", "What is KNIME?"),), temperature=0.0)


# --- settings: local unless the owner opts in ---------------------------------------------


def test_the_local_model_is_the_default() -> None:
    settings = settings_from_environment({})
    assert settings.model_provider == "lm_studio"
    assert settings.gemini_model is None
    assert active_model_identifier(settings) == "qwen/qwen3.5-4b"


def test_gemini_needs_a_model_name_and_a_key_in_the_environment() -> None:
    env = {"AAOS_MODEL_PROVIDER": "gemini", "GEMINI_API_KEY": KEY, "AAOS_GEMINI_MODEL": "m-1"}
    settings = settings_from_environment(env)
    assert (settings.model_provider, settings.gemini_model) == ("gemini", "m-1")

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        settings_from_environment({"AAOS_MODEL_PROVIDER": "gemini", "AAOS_GEMINI_MODEL": "m-1"})
    with pytest.raises(ValueError, match="AAOS_GEMINI_MODEL"):
        settings_from_environment({"AAOS_MODEL_PROVIDER": "gemini", "GEMINI_API_KEY": KEY})
    with pytest.raises(ValueError, match="AAOS_MODEL_PROVIDER"):
        settings_from_environment({"AAOS_MODEL_PROVIDER": "openai"})


def test_the_api_key_is_never_part_of_the_settings() -> None:
    env = {"AAOS_MODEL_PROVIDER": "gemini", "GEMINI_API_KEY": KEY, "AAOS_GEMINI_MODEL": "m-1"}
    settings = settings_from_environment(env)

    assert KEY not in repr(settings)
    assert KEY not in json.dumps(
        {name: str(getattr(settings, name)) for name in settings.__slots__}
    )


def test_a_gemini_model_without_the_gemini_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="gemini_model"):
        AppSettings(gemini_model="gemini-test-flash")


def test_the_endpoint_is_fixed_and_the_local_url_stays_loopback_only() -> None:
    assert GEMINI_BASE_URL == "https://generativelanguage.googleapis.com/v1beta/openai/"
    with pytest.raises(ValueError, match="loopback"):
        AppSettings(lm_studio_base_url="https://generativelanguage.googleapis.com")


# --- the gateway ---------------------------------------------------------------------------


def test_generate_sends_one_authenticated_request_to_gemini() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _ok("Visible answer")

    response = _run(_gateway(handler), REQUEST)

    assert response.content == "Visible answer"
    (request,) = seen
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.url.scheme == "https"
    assert request.url.path == "/v1beta/openai/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "gemini-test-flash"  # the "models/" prefix is dropped
    assert body["reasoning_effort"] == "none" and body["stream"] is False
    assert KEY not in request.content.decode()


def test_the_json_schema_is_reduced_to_what_gemini_supports() -> None:
    saw: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        saw.update(json.loads(request.content))
        return _ok('{"action": "ANSWER"}')

    request = ChatRequest(
        messages=(ChatMessage("user", "pick a tool"),),
        structured_output="json_schema",
        structured_output_schema="read_only_academic_decision",
    )
    _run(_gateway(handler), request)

    sent = json.dumps(saw["response_format"])
    for dropped in ("pattern", "additionalProperties", "maxLength", "minLength", "oneOf", "strict"):
        assert f'"{dropped}"' not in sent, dropped
    assert '"enum"' in sent and '"required"' in sent  # the closed vocabulary stays


def test_every_aaos_schema_can_be_reduced_without_changing_its_shape() -> None:
    for name, fmt in _STRUCTURED_RESPONSE_FORMATS.items():
        reduced = gemini_response_format(fmt)
        assert reduced["json_schema"]["name"] == fmt["json_schema"]["name"], name
        schema = reduced["json_schema"]["schema"]
        # Tool-choosing schemas are a oneOf of per-tool objects; Gemini gets it as anyOf.
        assert schema.get("type") == "object" or "anyOf" in schema, name
        # Property names are data, not keywords: none may be lost.
        original = fmt["json_schema"]["schema"].get("properties", {})
        assert set(reduced["json_schema"]["schema"].get("properties", {})) == set(original), name


def test_availability_lists_the_account_models() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1beta/openai/models"
        return httpx2.Response(
            200, json={"data": [{"id": "models/gemini-test-flash"}, {"id": "models/other"}]}
        )

    async def scenario():  # type: ignore[no-untyped-def]
        gateway = _gateway(handler)
        try:
            return await gateway.check_availability()
        finally:
            await gateway.aclose()

    availability = asyncio.run(scenario())
    assert availability.available and availability.configured_model == "gemini-test-flash"
    assert "other" in availability.available_models


@pytest.mark.parametrize(
    ("status", "error_type"),
    (
        (401, InferenceBackendError),
        (403, InferenceBackendError),
        (404, ModelUnavailableError),
        (429, InferenceBackendError),
        (500, InferenceBackendError),
        (400, InferenceBackendError),
    ),
)
def test_http_failures_are_typed_and_never_reveal_the_key(
    status: int, error_type: type[InferenceError]
) -> None:
    events: list[LocalInferenceTelemetryEvent] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        # A hostile or buggy backend that echoes secrets back.
        return httpx2.Response(
            status,
            json={"error": {"status": "INVALID_ARGUMENT", "message": f"bad {KEY} request"}},
        )

    with pytest.raises(error_type) as caught:
        _run(_gateway(handler, events), REQUEST)

    assert KEY not in str(caught.value) and KEY not in repr(caught.value)
    assert events and all(KEY not in json.dumps(event.as_dict()) for event in events)
    assert events[-1].http_status == status
    assert events[-1].backend_category.startswith(f"http_{status}")  # type: ignore[union-attr]


def test_a_google_error_status_is_kept_only_as_a_safe_token() -> None:
    events: list[LocalInferenceTelemetryEvent] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(handler, events), REQUEST)
    assert events[-1].backend_category == "http_400_INVALID_ARGUMENT"

    events.clear()

    def hostile(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"status": f"x {KEY}"}})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(hostile, events), REQUEST)
    assert events[-1].backend_category == "http_400"


def test_network_failures_and_garbage_replies_are_typed() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach, key {KEY}", request=request)

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    def garbage(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"unexpected": True})

    with pytest.raises(InferenceConnectionError) as connect:
        _run(_gateway(refuse), REQUEST)
    assert KEY not in str(connect.value)
    with pytest.raises(InferenceTimeoutError):
        _run(_gateway(slow), REQUEST)
    with pytest.raises(InferenceProtocolError):
        _run(_gateway(garbage), REQUEST)


def test_the_gateway_repr_and_a_closed_gateway_hide_and_refuse() -> None:
    gateway = _gateway(lambda request: _ok())
    assert KEY not in repr(gateway)

    async def scenario() -> None:
        await gateway.aclose()
        await gateway.aclose()  # idempotent
        with pytest.raises(InferenceError):
            await gateway.generate(REQUEST)

    asyncio.run(scenario())


def test_redirects_are_never_followed() -> None:
    def redirect(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(redirect), REQUEST)


def test_a_rejected_schema_is_retried_once_without_it() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx2.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return _ok('{"action": "ANSWER"}')

    request = ChatRequest(
        messages=(ChatMessage("user", "pick a tool"),),
        structured_output="json_schema",
        structured_output_schema="read_only_academic_decision",
    )
    response = _run(_gateway(handler), request)

    assert response.content == '{"action": "ANSWER"}'
    assert ["response_format" in body for body in bodies] == [True, False]


def test_only_a_400_is_retried_and_never_with_more_than_before() -> None:
    calls: list[int] = []

    def server_error(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(500)

    structured = ChatRequest(
        messages=(ChatMessage("user", "x"),),
        structured_output="json_schema",
        structured_output_schema="read_only_academic_decision",
    )
    with pytest.raises(InferenceBackendError):
        _run(_gateway(server_error), structured)
    assert calls == [1]  # a 500 is not retried

    calls.clear()

    def plain_400(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(400)

    with pytest.raises(InferenceBackendError):
        _run(_gateway(plain_400), REQUEST)
    assert calls == [1, 1]  # a plain request is retried once, without reasoning_effort


def test_a_model_that_rejects_reasoning_effort_is_retried_without_it() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx2.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return _ok("fine")

    assert _run(_gateway(handler), REQUEST).content == "fine"
    assert ["reasoning_effort" in body for body in bodies] == [True, False]


def test_a_structured_request_walks_down_to_the_plainest_one_that_works() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body or "reasoning_effort" in body:
            return httpx2.Response(400)
        return _ok('{"action": "ANSWER"}')

    structured = ChatRequest(
        messages=(ChatMessage("user", "pick a tool"),),
        structured_output="json_schema",
        structured_output_schema="read_only_academic_decision",
    )
    _run(_gateway(handler), structured)

    assert [("response_format" in b, "reasoning_effort" in b) for b in bodies] == [
        (True, True),
        (False, True),
        (False, False),
    ]


def test_a_failure_is_logged_with_its_safe_facts_in_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": f"quota {KEY}"}}
        )

    with caplog.at_level(logging.WARNING, logger="aaos.inference.gemini"):
        with pytest.raises(InferenceBackendError):
            _run(_gateway(handler), REQUEST)

    assert "http_status=429" in caplog.text
    assert "http_429_RESOURCE_EXHAUSTED" in caplog.text
    assert "INFERENCE_BACKEND_ERROR" in caplog.text
    assert KEY not in caplog.text and "What is KNIME" not in caplog.text


def test_a_short_overload_is_waited_out() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx2.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return _ok("finally")

    assert _run(_gateway(handler), REQUEST).content == "finally"
    assert len(calls) == 3  # two 503s, then the answer


def test_a_lasting_overload_fails_after_two_retries() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(handler), REQUEST)
    assert len(calls) == 3


def test_only_a_503_is_waited_on_not_a_rate_limit_or_a_bad_key() -> None:
    for status in (401, 403, 404, 429, 500):
        calls: list[int] = []

        def handler(request: httpx2.Request, code: int = status) -> httpx2.Response:
            calls.append(1)
            return httpx2.Response(code)

        with pytest.raises(InferenceError):
            _run(_gateway(handler), REQUEST)
        assert len(calls) == 1, status


def test_a_reply_with_extra_fields_and_odd_usage_is_still_read() -> None:
    """Only the answer text matters; whatever else Google adds must not fail an answer."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "model": "models/gemini-test-flash",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "KNIME is a data analytics platform.",
                            "reasoning_content": "thinking summary Google may add",
                            "extra_content": {"google": {"thought": True}},
                            "tool_calls": [],
                        },
                    }
                ],
                # Thinking models can report usage without a completion count.
                "usage": {"prompt_tokens": 12, "total_tokens": 80},
            },
        )

    response = _run(_gateway(handler), REQUEST)

    assert response.content == "KNIME is a data analytics platform."
    assert response.model_identifier == "models/gemini-test-flash"
    assert response.usage is None  # unreadable usage is dropped, not fatal


def test_content_given_as_parts_is_joined() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": [{"text": "A "}, {"text": "B"}]}}
                ]
            },
        )

    assert _run(_gateway(handler), REQUEST).content == "A B"


def test_an_empty_answer_is_reported_with_its_finish_reason() -> None:
    summaries: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"role": "assistant", "content": None}}
                ]
            },
        )

    with pytest.raises(InferenceProtocolError):
        _run(_gateway(handler, on_failure=summaries.append), REQUEST)

    assert summaries and "empty_content finish_reason=length" in summaries[-1]


def test_failures_and_successes_are_reported_in_safe_words() -> None:
    failures: list[str] = []
    successes: list[int] = []

    def limited(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": f"quota {KEY}"}}
        )

    with pytest.raises(InferenceBackendError):
        _run(_gateway(limited, on_failure=failures.append), REQUEST)
    assert failures == ["Gemini generation failed: http_429_RESOURCE_EXHAUSTED"]
    assert KEY not in failures[0]

    _run(_gateway(lambda request: _ok(), on_success=lambda: successes.append(1)), REQUEST)
    assert successes == [1]


def test_a_broken_report_callback_never_breaks_an_answer() -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("callback bug")

    assert (
        _run(_gateway(lambda request: _ok("fine"), on_success=explode), REQUEST).content == "fine"
    )

    with pytest.raises(InferenceBackendError):
        _run(_gateway(lambda request: httpx2.Response(401), on_failure=explode), REQUEST)


# --- choosing the gateway ------------------------------------------------------------------


def test_the_default_gateway_is_the_local_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    gateway = build_model_gateway(AppSettings())
    try:
        assert isinstance(gateway, LMStudioGateway)
    finally:
        asyncio.run(gateway.aclose())


def test_gemini_is_built_only_with_a_key_and_warns_loudly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(InferenceError, match="GEMINI_API_KEY"):
        build_model_gateway(SETTINGS)

    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    with caplog.at_level(logging.WARNING, logger="aaos.inference"):
        gateway = build_model_gateway(SETTINGS)
    try:
        assert isinstance(gateway, GeminiGateway)
    finally:
        asyncio.run(gateway.aclose())
    assert "CLOUD MODEL ACTIVE" in caplog.text and KEY not in caplog.text


def test_no_warning_for_the_local_model(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="aaos.inference"):
        warn_if_cloud_model(AppSettings())
    assert caplog.text == ""


def test_the_rag_runtime_gets_a_no_op_lifecycle_for_gemini(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    from aaos.rag import factory

    captured: dict[str, object] = {}

    def fake_build(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(service=object(), close=lambda: None)

    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(factory, "validate_rag_database", lambda path: None, raising=False)
    monkeypatch.setattr(factory, "build_phase4_runtime", fake_build)

    configured = factory.build_configured_phase4_runtime(SETTINGS, data_root=tmp_path.resolve())
    try:
        assert captured["model_lifecycle_factory"] is NoOpModelLifecycle
        assert isinstance(captured["gateway"], GeminiGateway)
        assert captured["summary_model_identifier"] == "gemini-test-flash"
    finally:
        configured.close()


def test_the_local_rag_runtime_keeps_the_local_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    from aaos.rag import factory

    captured: dict[str, object] = {}

    def fake_build(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(service=object(), close=lambda: None)

    monkeypatch.setattr(factory, "validate_rag_database", lambda path: None, raising=False)
    monkeypatch.setattr(factory, "build_phase4_runtime", fake_build)

    configured = factory.build_configured_phase4_runtime(
        AppSettings(), data_root=tmp_path.resolve()
    )
    try:
        assert "model_lifecycle_factory" not in captured
        assert isinstance(captured["gateway"], LMStudioGateway)
    finally:
        configured.close()
