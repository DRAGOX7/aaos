"""Probe the configured Gemini model with AAOS's own requests and print what happened.

Run it in the terminal where GEMINI_API_KEY and AAOS_GEMINI_MODEL are set:

    .venv\\Scripts\\python.exe scripts\\gemini_probe.py

It sends one small request per structured-output format that the document-answering
and agent steps use, then two with a long prompt, through the same gateway the
backend uses (same settings, retries and fallbacks). One line per request says whether
it worked and, if not, the safe failure words (an HTTP status and Google's status
word). It prints no prompt, no reply and no key.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence

import httpx2

from aaos.config import GEMINI_API_KEY_ENVIRONMENT_KEY, GEMINI_MODEL_ENVIRONMENT_KEY, AppSettings
from aaos.inference.gateway import InferenceError
from aaos.inference.gemini import GeminiGateway
from aaos.inference.models import ChatMessage, ChatRequest

SCHEMAS = (
    "atomic_requirement_decomposition",
    "evidence_segments_single_requirement",
    "requirement_coverage",
    "grounded_answer",
    "read_only_academic_decision",
)
LONG_PROMPT_SCHEMAS = ("atomic_requirement_decomposition", "grounded_answer")
FILLER = "KNIME is a data analytics platform with a graphical workflow editor. " * 120


async def probe_one(
    gateway: GeminiGateway, failures: list[str], schema: str, *, long_prompt: bool
) -> str:
    """Send one structured request and describe the outcome in safe words."""
    failures.clear()
    prompt = (FILLER if long_prompt else "") + "Question: what is KNIME?"
    request = ChatRequest(
        messages=(
            ChatMessage("system", "Answer with JSON that follows the requested schema."),
            ChatMessage("user", prompt),
        ),
        structured_output="json_schema",
        structured_output_schema=schema,  # type: ignore[arg-type]
        max_output_tokens=512,
        temperature=0.0,
    )
    label = f"{schema}{' (long prompt)' if long_prompt else ''}"
    try:
        reply = await gateway.generate(request)
    except InferenceError:
        last = failures[-1] if failures else "no detail"
        return f"{label:<62} FAILED  {last}"
    earlier = f" after {len(failures)} rejected attempt(s): {failures[-1]}" if failures else ""
    return f"{label:<62} OK      {len(reply.content)} characters{earlier}"


async def run_probe(gateway: GeminiGateway, failures: list[str]) -> list[str]:
    lines = [await probe_one(gateway, failures, name, long_prompt=False) for name in SCHEMAS]
    for name in LONG_PROMPT_SCHEMAS:
        lines.append(await probe_one(gateway, failures, name, long_prompt=True))
    return lines


async def _main() -> int:
    key = os.environ.get(GEMINI_API_KEY_ENVIRONMENT_KEY, "").strip()
    model = os.environ.get(GEMINI_MODEL_ENVIRONMENT_KEY, "").strip()
    if not key or not model:
        print(f"Set {GEMINI_API_KEY_ENVIRONMENT_KEY} and {GEMINI_MODEL_ENVIRONMENT_KEY} first.")
        return 2
    failures: list[str] = []
    gateway = GeminiGateway(
        AppSettings(model_provider="gemini", gemini_model=model),
        httpx2.AsyncHTTPTransport(),
        api_key=key,
        overload_retry_delays=(1.0, 3.0),
        rate_limit_retries=0,  # show a 429 as it is instead of waiting it out
        on_failure=failures.append,
    )
    try:
        print(f"Probing {model} with AAOS's own requests...\n")
        for line in await run_probe(gateway, failures):
            print(line)
    finally:
        await gateway.aclose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
