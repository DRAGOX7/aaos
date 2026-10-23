"""Probe the signed-in ChatGPT plan with AAOS's request shapes and print what happened.

Phase 11, slice 11C. Run it by hand, with the owner present, after a real sign-in:

    .venv\\Scripts\\aaos.exe model chatgpt-plan sign-in
    .venv\\Scripts\\python.exe scripts\\chatgpt_plan_probe.py [MODEL]

It lists the plan's models, then sends short synthetic prompts (never course material):
one plain request, the system text as a developer message and as ``instructions``,
three reasoning-effort values, and one request per structured-output shape the
document-answering steps use. Last, the same shapes go through the real gateway, with
its retries and fallbacks. One line per request says whether it worked and, if not,
OpenAI's safe words (HTTP status, error code, error param). It prints no prompt, no
reply and no token.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Sequence
from typing import Any

import httpx2

from aaos.config import AppSettings
from aaos.inference.chatgpt_plan import (
    API_BASE_URL,
    ChatGPTPlanGateway,
    _error_code,
    _safe,
    _sse_events,
    _usage,
    build_payload,
    fetch_chatgpt_plan_model_names,
)
from aaos.inference.chatgpt_plan_credentials import (
    ChatGPTCredentialStore,
    ChatGPTPlanUnavailable,
    credential_path_from_environment,
)
from aaos.inference.chatgpt_plan_signin import AuthClient
from aaos.inference.gateway import InferenceError
from aaos.inference.models import ChatMessage, ChatRequest

SCHEMAS = (
    "grounded_answer",
    "requirement_decomposition",
    "atomic_requirement_decomposition",
    "evidence_spans",
    "evidence_segments_single_requirement",
    "requirement_coverage",
)
REASONING_EFFORTS = ("none", "minimal", "low")
SYSTEM = "Answer briefly. When a JSON schema is given, answer with JSON that follows it."
QUESTION = "Question: what is KNIME? (Synthetic probe question; no course material.)"


def _plain(system: bool = False) -> ChatRequest:
    messages = (ChatMessage("system", SYSTEM),) if system else ()
    return ChatRequest(messages=(*messages, ChatMessage("user", QUESTION)), max_output_tokens=256)


def _structured(schema: str) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage("system", SYSTEM), ChatMessage("user", QUESTION)),
        structured_output="json_schema",
        structured_output_schema=schema,  # type: ignore[arg-type]
        max_output_tokens=512,
    )


def raw_variants(model: str) -> list[tuple[str, dict[str, Any]]]:
    """Each label with the exact body it sends."""
    variants = [
        (
            "plain user message",
            build_payload(model, _plain(), with_schema=False, reasoning_effort=None),
        ),
        (
            "system text as a developer message",
            build_payload(model, _plain(system=True), with_schema=False, reasoning_effort=None),
        ),
    ]
    with_instructions = build_payload(model, _plain(), with_schema=False, reasoning_effort=None)
    with_instructions["instructions"] = SYSTEM
    variants.append(("system text as instructions", with_instructions))
    for effort in REASONING_EFFORTS:
        payload = build_payload(model, _plain(), with_schema=False, reasoning_effort=None)
        payload["reasoning"] = {"effort": effort}
        variants.append((f"reasoning effort {effort}", payload))
    for schema in SCHEMAS:
        variants.append(
            (
                f"json_schema {schema}",
                build_payload(model, _structured(schema), with_schema=True, reasoning_effort=None),
            )
        )
    return variants


async def probe_raw(
    client: httpx2.AsyncClient, token: str, label: str, payload: dict[str, Any]
) -> str:
    """Send one body as is and describe the outcome in safe words."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    try:
        async with client.stream("POST", "responses", json=payload, headers=headers) as response:
            if not response.is_success:
                code, param = _error_code(await response.aread())
                return f"{label:<52} FAILED  http_{response.status_code} code={code} param={param}"
            size = 0
            outcome: str | None = None
            # Read to the end of the stream so every reader finishes normally.
            async with contextlib.aclosing(_sse_events(response)) as events:
                async for event in events:
                    if outcome is not None:
                        continue
                    kind = event.get("type")
                    delta = event.get("delta")
                    if kind == "response.output_text.delta" and isinstance(delta, str):
                        size += len(delta)
                    elif kind == "response.completed":
                        _, visible = _usage(event.get("response"))
                        outcome = f"OK      {size} characters, visible_tokens={visible}"
                    elif kind in {"response.failed", "response.incomplete", "error"}:
                        source = event.get("response")
                        error = (source if isinstance(source, dict) else event).get("error")
                        code = _safe(error.get("code")) if isinstance(error, dict) else None
                        outcome = f"FAILED  {kind} code={code}"
            return f"{label:<52} {outcome or 'FAILED  stream ended before completion'}"
    except httpx2.HTTPError as error:
        return f"{label:<52} FAILED  {type(error).__name__}"


async def probe_gateway(gateway: ChatGPTPlanGateway, failures: list[str], schema: str) -> str:
    failures.clear()
    label = f"gateway {schema}"
    try:
        reply = await gateway.generate(_structured(schema))
    except InferenceError:
        last = failures[-1] if failures else "no detail"
        return f"{label:<52} FAILED  {last}"
    earlier = f" after {len(failures)} rejected attempt(s): {failures[-1]}" if failures else ""
    return f"{label:<52} OK      {len(reply.content)} characters{earlier}"


async def run_probe(
    store: ChatGPTCredentialStore,
    model: str | None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[str]:
    lines: list[str] = []
    names = await fetch_chatgpt_plan_model_names(store, transport)
    lines.append(f"models offered: {', '.join(names) if names else '(none)'}")
    chosen = model or (names[0] if names else None)
    if chosen is None:
        lines.append("no model to probe")
        return lines
    lines.append(f"probing model: {chosen}")
    token = await store.access_token()
    async with httpx2.AsyncClient(
        transport=transport or httpx2.AsyncHTTPTransport(),
        base_url=API_BASE_URL,
        timeout=60.0,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for label, payload in raw_variants(chosen):
            lines.append(await probe_raw(client, token, label, payload))
    failures: list[str] = []
    gateway = ChatGPTPlanGateway(
        AppSettings(model_provider="chatgpt_plan", chatgpt_plan_model=chosen),
        transport or httpx2.AsyncHTTPTransport(),
        store=store,
        unavailable_retry_delays=(1.0,),
        on_failure=failures.append,
    )
    try:
        for schema in SCHEMAS:
            lines.append(await probe_gateway(gateway, failures, schema))
    finally:
        await gateway.aclose()
    return lines


async def _main(model: str | None) -> int:
    try:
        store = ChatGPTCredentialStore(credential_path_from_environment())
    except ChatGPTPlanUnavailable:
        print("The credential path must be absolute and outside the AAOS repository.")
        return 2
    if store.state()[0] != "SIGNED_IN":
        print("Sign in first: aaos model chatgpt-plan sign-in")
        return 2
    auth = AuthClient()
    store.set_refresher(auth.refresh)
    try:
        print("Probing the ChatGPT plan with synthetic prompts (no course material)...\n")
        for line in await run_probe(store, model):
            print(line)
    except InferenceError as error:
        print(f"Probe stopped: {type(error).__name__} {getattr(error, 'category', '')}".strip())
        return 1
    finally:
        await auth.aclose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_main(arguments[0] if arguments else None))


if __name__ == "__main__":
    sys.exit(main())
