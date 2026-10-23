"""Phase 11C: the probe reports each request in safe words and never prints a prompt or token."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx2

from aaos.inference.chatgpt_plan_credentials import (
    PLAN_USAGE_SCOPE,
    ChatGPTCredentialStore,
    TokenSet,
)
from scripts.chatgpt_plan_probe import QUESTION, SCHEMAS, SYSTEM, raw_variants, run_probe

ACCESS = "access-SECRET-probe-token"


def _store(tmp_path: Path) -> ChatGPTCredentialStore:
    store = ChatGPTCredentialStore(tmp_path / "plan.json")
    store.save_sign_in(
        client_id="oaiapp_test",
        account_subject="sub",
        tokens=TokenSet(ACCESS, "refresh-SECRET", 9_999_999_999.0, (PLAN_USAGE_SCOPE,)),
    )
    return store


def _completed(text: str = '{"status": "INSUFFICIENT_EVIDENCE"}') -> httpx2.Response:
    events = [
        {"type": "response.output_text.delta", "delta": text},
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 5, "output_tokens": 4}},
        },
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    return httpx2.Response(200, content=body)


def handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.path.endswith("/models"):
        return httpx2.Response(200, json={"models": [{"slug": "gpt-test", "visibility": "list"}]})
    body = json.loads(request.content)
    if body.get("reasoning", {}).get("effort") == "minimal":
        return httpx2.Response(
            400,
            json={
                "error": {
                    "code": "subscription_sharing_unsupported_capability",
                    "param": "reasoning.effort",
                    "message": ACCESS,
                }
            },
        )
    return _completed()


def test_the_probe_covers_every_shape_and_says_what_happened_in_safe_words(tmp_path: Path) -> None:
    lines = asyncio.run(run_probe(_store(tmp_path), None, httpx2.MockTransport(handler)))
    text = "\n".join(lines)
    assert lines[0] == "models offered: gpt-test"
    assert "probing model: gpt-test" in text
    assert any(
        "reasoning effort minimal" in line
        and "FAILED  http_400 code=subscription_sharing_unsupported_capability param=reasoning.effort"
        in line
        for line in lines
    )
    for schema in SCHEMAS:
        assert any(f"json_schema {schema}" in line and " OK " in line for line in lines)
        assert any(f"gateway {schema}" in line and " OK " in line for line in lines)
    for secret in (ACCESS, QUESTION, SYSTEM, "INSUFFICIENT_EVIDENCE"):
        assert secret not in text


def test_every_probe_body_is_plain_unstored_and_without_tools() -> None:
    for label, body in raw_variants("gpt-test"):
        assert body["stream"] is True and body["store"] is False, label
        assert "tools" not in body and "temperature" not in body, label
        assert "max_output_tokens" not in body, label
