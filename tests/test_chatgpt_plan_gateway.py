"""Phase 11D: the ChatGPT-plan gateway is a plain, capped, streamed model call."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from aaos.config import AppSettings
from aaos.inference.chatgpt_plan import (
    API_BASE_URL,
    ChatGPTPlanGateway,
    fetch_chatgpt_plan_model_names,
)
from aaos.inference.chatgpt_plan_credentials import (
    PLAN_USAGE_SCOPE,
    ChatGPTCredentialStore,
    ChatGPTPlanUnavailable,
    TokenSet,
)
from aaos.inference.factory import NoOpModelLifecycle, build_model_gateway, warn_if_cloud_model
from aaos.inference.gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceContextLimitError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)
from aaos.inference.models import ChatMessage, ChatRequest
from aaos.inference.telemetry import LocalInferenceTelemetryEvent

ACCESS = "access-SECRET-token-0123456789"
NEW_ACCESS = "access-SECRET-after-refresh"
MODEL = "gpt-test-plan"
SETTINGS = AppSettings(model_provider="chatgpt_plan", chatgpt_plan_model=MODEL)
SCOPES = ("email", "offline_access", "openid", PLAN_USAGE_SCOPE)
REQUEST = ChatRequest(
    messages=(ChatMessage("system", "Be brief."), ChatMessage("user", "What is KNIME?")),
    max_output_tokens=64,
    temperature=0.0,
)


class Refresher:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, client_id: str, refresh_token: str) -> TokenSet:
        self.calls += 1
        return TokenSet(NEW_ACCESS, "refresh-rotated", 9_999_999_999.0, SCOPES)


def _store(tmp_path: Path, refresher: Refresher | None = None) -> ChatGPTCredentialStore:
    store = ChatGPTCredentialStore(
        tmp_path / "plan.json", refresher=refresher or Refresher(), lock_poll_seconds=0.01
    )
    store.save_sign_in(
        client_id="oaiapp_test",
        account_subject="sub",
        tokens=TokenSet(ACCESS, "refresh-SECRET", 9_999_999_999.0, SCOPES),
    )
    return store


def _sse(*events: dict[str, Any]) -> bytes:
    lines = []
    for event in events:
        lines.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(lines).encode("utf-8")


def _completed(
    text: str = "KNIME is a platform.", output_tokens: int = 6, reasoning_tokens: int = 0
) -> httpx2.Response:
    usage = {
        "input_tokens": 20,
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": 20 + output_tokens,
    }
    half = len(text) // 2
    body = _sse(
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_text.delta", "delta": text[:half]},
        {"type": "response.output_text.delta", "delta": text[half:]},
        {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}},
    )
    return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _gateway(
    tmp_path: Path,
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    refresher: Refresher | None = None,
    events: list[LocalInferenceTelemetryEvent] | None = None,
    failures: list[str] | None = None,
    store: ChatGPTCredentialStore | None = None,
) -> ChatGPTPlanGateway:
    return ChatGPTPlanGateway(
        SETTINGS,
        httpx2.MockTransport(handler),
        store=store or _store(tmp_path, refresher),
        diagnostic_sink=events.append if events is not None else None,
        unavailable_retry_delays=(0.0, 0.0),
        on_failure=failures.append if failures is not None else None,
    )


def _run(gateway: ChatGPTPlanGateway, request: ChatRequest = REQUEST) -> Any:
    async def scenario() -> Any:
        try:
            return await gateway.generate(request)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def _bodies(requests: list[httpx2.Request]) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in requests]


# --- the request ---------------------------------------------------------------------------


def test_a_request_is_a_plain_streamed_call_with_no_tools(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _completed()

    reply = _run(_gateway(tmp_path, handler))
    assert reply.content == "KNIME is a platform."
    assert reply.model_identifier == MODEL
    assert reply.usage.output_tokens == 6 and reply.usage.input_tokens == 20
    (request,) = seen
    assert str(request.url) == API_BASE_URL + "responses"
    assert request.headers["authorization"] == f"Bearer {ACCESS}"
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert body["stream"] is True and body["store"] is False
    assert body["input"] == [
        {"role": "developer", "content": "Be brief."},
        {"role": "user", "content": "What is KNIME?"},
    ]
    for forbidden in (
        "tools",
        "tool_choice",
        "temperature",
        "max_output_tokens",
        "top_p",
        "user",
        "truncation",
        "metadata",
        "previous_response_id",
    ):
        assert forbidden not in body


def test_a_structured_request_sends_the_aaos_schema(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _completed('{"status": "INSUFFICIENT_EVIDENCE"}')

    request = ChatRequest(
        messages=(ChatMessage("user", "q"),),
        structured_output="json_schema",
        structured_output_schema="grounded_answer",
        max_output_tokens=256,
    )
    _run(_gateway(tmp_path, handler), request)
    text_format = _bodies(seen)[0]["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["name"] == "aaos_grounded_rag_answer_v1"
    assert text_format["schema"]["type"] == "object"


def _rejected(param: str) -> httpx2.Response:
    return httpx2.Response(400, json={"error": {"code": "unsupported_value", "param": param}})


STRUCTURED = ChatRequest(
    messages=(ChatMessage("user", "q"),),
    structured_output="json_schema",
    structured_output_schema="grounded_answer",
    max_output_tokens=256,
)


def test_an_unnamed_rejection_is_retried_with_less_never_more(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if len(seen) < 5:
            return _rejected("x")
        return _completed('{"status": "INSUFFICIENT_EVIDENCE"}')

    _run(_gateway(tmp_path, handler), STRUCTURED)
    bodies = _bodies(seen)
    strictness = [b.get("text", {}).get("format", {}).get("strict") for b in bodies]
    assert [(s, b.get("reasoning", {}).get("effort")) for s, b in zip(strictness, bodies)] == [
        (True, "none"),  # strict schema
        (False, "none"),  # the same schema, not strict
        (None, "none"),  # no schema
        (None, "low"),
        (None, None),
    ]


def test_a_rejected_reasoning_effort_steps_down_keeps_the_schema_and_is_remembered(
    tmp_path: Path,
) -> None:
    # What the 2026-10-23 probe saw from gpt-6.1-sol: "none" refused, "low" accepted.
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if json.loads(request.content).get("reasoning", {}).get("effort") == "none":
            return _rejected("reasoning.effort")
        return _completed('{"status": "INSUFFICIENT_EVIDENCE"}')

    gateway = _gateway(tmp_path, handler)

    async def twice() -> None:
        try:
            await gateway.generate(STRUCTURED)
            await gateway.generate(STRUCTURED)
        finally:
            await gateway.aclose()

    asyncio.run(twice())
    bodies = _bodies(seen)
    assert [b["reasoning"]["effort"] for b in bodies] == ["none", "low", "low"]
    assert all("text" in b for b in bodies)  # the schema is never dropped for this


def test_a_rejected_schema_is_dropped_for_that_schema_only(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body.get("text", {}).get("format", {}).get("name") == "aaos_grounded_rag_answer_v1":
            return _rejected("text.format.schema")
        return _completed('{"status": "INSUFFICIENT_EVIDENCE"}')

    other = ChatRequest(
        messages=(ChatMessage("user", "q"),),
        structured_output="json_schema",
        structured_output_schema="requirement_coverage",
        max_output_tokens=256,
    )
    gateway = _gateway(tmp_path, handler)

    async def both() -> None:
        try:
            await gateway.generate(STRUCTURED)
            await gateway.generate(STRUCTURED)
            await gateway.generate(other)
        finally:
            await gateway.aclose()

    asyncio.run(both())
    bodies = _bodies(seen)
    strictness = [b.get("text", {}).get("format", {}).get("strict") for b in bodies]
    # strict, then not strict, then no schema; remembered for that schema only.
    assert strictness == [True, False, None, None, True]
    assert all(b["reasoning"]["effort"] == "none" for b in bodies)


def test_a_400_on_the_plainest_request_is_reported(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"code": "invalid_request"}})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(tmp_path, handler))


def test_reasoning_and_an_over_budget_prompt_are_refused_before_any_call(tmp_path: Path) -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return _completed()

    with pytest.raises(UnsupportedInferenceFeatureError):
        _run(_gateway(tmp_path, handler), ChatRequest(messages=REQUEST.messages, reasoning=True))
    huge = ChatRequest(messages=(ChatMessage("user", "word " * 9000),), max_output_tokens=512)
    with pytest.raises(InferenceContextLimitError):
        _run(_gateway(tmp_path, handler), huge)
    assert calls == []


# --- reading the stream --------------------------------------------------------------------


def test_a_stream_that_ends_without_completion_is_a_connection_failure(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = _sse({"type": "response.output_text.delta", "delta": "half an answ"})
        return httpx2.Response(200, content=body)

    with pytest.raises(InferenceConnectionError):
        _run(_gateway(tmp_path, handler))


def test_an_incomplete_response_is_not_used(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = _sse(
            {"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.incomplete", "response": {"incomplete_details": {}}},
        )
        return httpx2.Response(200, content=body)

    with pytest.raises(InferenceProtocolError):
        _run(_gateway(tmp_path, handler))


def test_visible_output_over_the_cap_is_rejected(tmp_path: Path) -> None:
    def too_many_bytes(request: httpx2.Request) -> httpx2.Response:
        return _completed("x" * (64 * 8 + 1), output_tokens=10)

    with pytest.raises(InferenceProtocolError):
        _run(_gateway(tmp_path, too_many_bytes))

    def too_many_tokens(request: httpx2.Request) -> httpx2.Response:
        return _completed("short", output_tokens=65)

    with pytest.raises(InferenceProtocolError):
        _run(_gateway(tmp_path, too_many_tokens))


def test_reasoning_tokens_do_not_count_against_the_visible_cap(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completed("short", output_tokens=500, reasoning_tokens=480)

    assert _run(_gateway(tmp_path, handler)).content == "short"


def test_text_given_only_in_the_completed_response_is_read(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        output = [{"type": "message", "content": [{"type": "output_text", "text": "Hi there"}]}]
        body = _sse({"type": "response.completed", "response": {"output": output}})
        return httpx2.Response(200, content=body)

    assert _run(_gateway(tmp_path, handler)).content == "Hi there"


# --- errors --------------------------------------------------------------------------------


def test_the_plan_limit_fails_at_once_and_is_never_retried(tmp_path: Path) -> None:
    for make in (
        lambda: httpx2.Response(
            429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded"}}
        ),
        lambda: httpx2.Response(
            200,
            content=_sse(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
                }
            ),
        ),
    ):
        calls: list[httpx2.Request] = []
        failures: list[str] = []

        def handler(request: httpx2.Request, make: Any = make) -> httpx2.Response:
            calls.append(request)
            return make()

        with pytest.raises(InferenceBackendError) as caught:
            _run(_gateway(tmp_path, handler, failures=failures))
        assert getattr(caught.value, "category", None) == "PLAN_LIMIT_REACHED"
        assert len(calls) == 1
        assert failures and "PLAN_LIMIT_REACHED" in failures[-1]


def test_a_refused_token_is_refreshed_once_then_needs_a_sign_in(tmp_path: Path) -> None:
    refresher = Refresher()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx2.Response(401, json={"detail": "expired"})
        return _completed()

    _run(_gateway(tmp_path, handler, refresher=refresher))
    assert seen == [f"Bearer {ACCESS}", f"Bearer {NEW_ACCESS}"]
    assert refresher.calls == 1

    def always_401(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"code": "subscription_sharing_invalid_user"}})

    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        _run(_gateway(tmp_path / "second", always_401))
    assert caught.value.category == "SIGN_IN_REQUIRED"


def test_a_short_outage_is_waited_out_and_a_lasting_one_fails(tmp_path: Path) -> None:
    calls: list[httpx2.Request] = []

    def flaky(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx2.Response(
                503, json={"error": {"code": "subscription_sharing_usage_unavailable"}}
            )
        return _completed()

    assert _run(_gateway(tmp_path, flaky)).content
    assert len(calls) == 3

    def down(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"detail": "unavailable"})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(tmp_path, down))


def test_an_ineligible_account_and_a_missing_model_are_typed(tmp_path: Path) -> None:
    def ineligible(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403, json={"error": {"code": "subscription_sharing_user_not_eligible"}}
        )

    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        _run(_gateway(tmp_path, ineligible))
    assert caught.value.category == "NOT_ELIGIBLE"

    def missing(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"error": {"code": "model_not_found"}})

    with pytest.raises(ModelUnavailableError):
        _run(_gateway(tmp_path, missing))


def test_without_a_sign_in_nothing_is_sent(tmp_path: Path) -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return _completed()

    empty = ChatGPTCredentialStore(tmp_path / "none.json", refresher=Refresher())
    with pytest.raises(ChatGPTPlanUnavailable) as caught:
        _run(_gateway(tmp_path, handler, store=empty))
    assert caught.value.category == "NOT_CONFIGURED"
    assert calls == []


def test_network_failures_and_timeouts_are_typed(tmp_path: Path) -> None:
    def offline(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(InferenceConnectionError):
        _run(_gateway(tmp_path, offline))

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(InferenceTimeoutError):
        _run(_gateway(tmp_path, slow))


def test_redirects_are_never_followed(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://evil.example/responses"})

    with pytest.raises(InferenceBackendError):
        _run(_gateway(tmp_path, handler))


def test_a_hostile_backend_cannot_make_the_token_appear(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        echoed = request.headers["authorization"]
        bare = echoed.removeprefix("Bearer ")
        return httpx2.Response(
            500, json={"error": {"code": bare, "param": bare, "message": echoed}, "detail": echoed}
        )

    events: list[LocalInferenceTelemetryEvent] = []
    failures: list[str] = []
    with caplog.at_level(logging.WARNING):
        with pytest.raises(InferenceBackendError) as caught:
            _run(_gateway(tmp_path, handler, events=events, failures=failures))
    shown = " ".join(
        [str(caught.value), repr(caught.value), caplog.text, " ".join(failures)]
        + [json.dumps(event.as_dict()) for event in events]
    )
    assert ACCESS not in shown
    gateway = _gateway(tmp_path, handler)
    assert ACCESS not in repr(gateway)
    asyncio.run(gateway.aclose())


# --- models and wiring ---------------------------------------------------------------------


def test_availability_and_the_model_list_keep_only_listed_models(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == API_BASE_URL + "models"
        return httpx2.Response(
            200,
            json={
                "models": [
                    {"slug": MODEL, "display_name": "Test", "visibility": "list"},
                    {"slug": "gpt-hidden", "visibility": "hide"},
                    {"slug": "gpt-other", "visibility": "list"},
                ]
            },
        )

    gateway = _gateway(tmp_path, handler)

    async def scenario() -> Any:
        try:
            return await gateway.check_availability()
        finally:
            await gateway.aclose()

    availability = asyncio.run(scenario())
    assert availability.available is True
    assert availability.available_models == (MODEL, "gpt-other")
    names = asyncio.run(
        fetch_chatgpt_plan_model_names(_store(tmp_path / "list"), httpx2.MockTransport(handler))
    )
    assert names == (MODEL, "gpt-other")


def test_the_gateway_is_built_for_the_provider_and_warns_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AAOS_CHATGPT_PLAN_CREDENTIAL_PATH", str(tmp_path / "plan.json"))
    with caplog.at_level(logging.WARNING, logger="aaos.inference"):
        gateway = build_model_gateway(SETTINGS)
        warn_if_cloud_model(SETTINGS)
    try:
        assert isinstance(gateway, ChatGPTPlanGateway)
    finally:
        asyncio.run(gateway.aclose())
    assert "CLOUD MODEL ACTIVE" in caplog.text and "OpenAI" in caplog.text


def test_the_rag_runtime_gets_a_no_op_lifecycle_and_names_the_plan_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aaos.rag import factory

    captured: dict[str, object] = {}

    def fake_build(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(service=object(), close=lambda: None)

    monkeypatch.setenv("AAOS_CHATGPT_PLAN_CREDENTIAL_PATH", str(tmp_path / "plan.json"))
    monkeypatch.setattr(factory, "validate_rag_database", lambda path: None, raising=False)
    monkeypatch.setattr(factory, "build_phase4_runtime", fake_build)
    configured = factory.build_configured_phase4_runtime(SETTINGS, data_root=tmp_path.resolve())
    try:
        assert captured["model_lifecycle_factory"] is NoOpModelLifecycle
        assert isinstance(captured["gateway"], ChatGPTPlanGateway)
        assert captured["summary_model_identifier"] == MODEL
        decomposer = captured["atomic_requirement_decomposer_factory"]
        assert callable(decomposer)
    finally:
        configured.close()


# --- union schemas (Phase 11 defect fix, 2026-10-23) -----------------------------------------


def _structured(schema: str) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage("user", "q"),),
        structured_output="json_schema",
        structured_output_schema=schema,  # type: ignore[arg-type]
        max_output_tokens=512,
    )


def test_a_union_schema_is_wrapped_in_an_object_and_the_reply_unwrapped(tmp_path: Path) -> None:
    # OpenAI refuses a top-level oneOf; AAOS's agent decision schemas have one.
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _completed('{"decision": {"kind": "example", "value": 1}}')

    reply = _run(_gateway(tmp_path, handler), _structured("calendar_proposal_decision"))
    sent = _bodies(seen)[0]["text"]["format"]
    assert sent["schema"]["type"] == "object"
    assert sent["schema"]["required"] == ["decision"]
    assert sent["schema"]["additionalProperties"] is False
    assert '"oneOf"' not in json.dumps(sent["schema"])
    assert json.loads(reply.content) == {"kind": "example", "value": 1}


def test_an_object_schema_is_sent_unwrapped_and_its_reply_untouched(tmp_path: Path) -> None:
    from aaos.inference.lm_studio import _STRUCTURED_RESPONSE_FORMATS

    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return _completed('{"decision": "kept as is"}')

    reply = _run(_gateway(tmp_path, handler), _structured("grounded_answer"))
    sent = _bodies(seen)[0]["text"]["format"]
    original = _STRUCTURED_RESPONSE_FORMATS["grounded_answer"]["json_schema"]["schema"]
    assert sent["schema"] == original and sent["strict"] is True
    assert reply.content == '{"decision": "kept as is"}'


def test_every_aaos_schema_is_sent_with_an_object_root_and_no_one_of() -> None:
    from aaos.inference.chatgpt_plan import openai_schema
    from aaos.inference.lm_studio import _STRUCTURED_RESPONSE_FORMATS

    for name, wrapper in _STRUCTURED_RESPONSE_FORMATS.items():
        schema, wrapped = openai_schema(name)
        assert schema["type"] == "object", name
        assert "anyOf" not in schema and "oneOf" not in schema, name
        assert '"oneOf"' not in json.dumps(schema), name
        if not wrapped:
            # Only the oneOf keyword is renamed; nothing else changes.
            original = json.dumps(wrapper["json_schema"]["schema"], sort_keys=True)
            assert json.dumps(schema, sort_keys=True) == original.replace('"oneOf"', '"anyOf"'), (
                name
            )


def test_a_wrapped_reply_that_is_not_the_expected_shape_is_left_for_aaos_to_reject(
    tmp_path: Path,
) -> None:
    for text in ("not json at all", '{"decision": 1, "extra": 2}', '["decision"]'):

        def handler(request: httpx2.Request, text: str = text) -> httpx2.Response:
            return _completed(text)

        reply = _run(_gateway(tmp_path, handler), _structured("calendar_proposal_decision"))
        assert reply.content == text
