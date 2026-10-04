"""The Gemini probe describes each request in safe words and never prints a prompt or key."""

from __future__ import annotations

import asyncio
import json

import httpx2

from aaos.config import AppSettings
from aaos.inference.gemini import GeminiGateway
from scripts.gemini_probe import LONG_PROMPT_SCHEMAS, SCHEMAS, run_probe

KEY = "AIzaSyTEST-not-a-real-key-0123456789"
SETTINGS = AppSettings(model_provider="gemini", gemini_model="gemini-test-flash")


def _lines(handler) -> list[str]:  # type: ignore[no-untyped-def]
    async def scenario() -> list[str]:
        failures: list[str] = []
        gateway = GeminiGateway(
            SETTINGS,
            httpx2.MockTransport(handler),
            api_key=KEY,
            overload_retry_delays=(0.0,),
            on_failure=failures.append,
        )
        try:
            return await run_probe(gateway, failures)
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def _ok(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": '{"a": 1}'}}]}
    )


def test_one_line_per_request_and_every_format_is_covered() -> None:
    lines = _lines(_ok)

    assert len(lines) == len(SCHEMAS) + len(LONG_PROMPT_SCHEMAS)
    assert all("OK" in line for line in lines)
    for name in SCHEMAS:
        assert any(line.startswith(name) for line in lines)
    assert sum("(long prompt)" in line for line in lines) == len(LONG_PROMPT_SCHEMAS)


def test_a_rejected_schema_that_works_without_it_is_reported_as_a_rejected_attempt() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if "response_format" in json.loads(request.content):
            return httpx2.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return _ok(request)

    lines = _lines(handler)

    assert all("OK" in line and "1 rejected attempt(s)" in line for line in lines)
    assert all("http_400_INVALID_ARGUMENT" in line for line in lines)


def test_a_lasting_overload_is_reported_with_its_status_word() -> None:
    lines = _lines(lambda request: httpx2.Response(503, json={"error": {"status": "UNAVAILABLE"}}))

    assert all("FAILED" in line and "http_503_UNAVAILABLE" in line for line in lines)


def test_the_probe_never_prints_the_prompt_or_the_key() -> None:
    lines = _lines(
        lambda request: httpx2.Response(
            400, json={"error": {"status": "INVALID_ARGUMENT", "message": f"bad {KEY} KNIME"}}
        )
    )

    text = "\n".join(lines)
    assert KEY not in text and "what is KNIME" not in text and "workflow editor" not in text
