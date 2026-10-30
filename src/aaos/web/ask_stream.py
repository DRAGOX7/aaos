"""Server-Sent Events transport for one Ask request.

The stream changes only how the answer is delivered. It sends coarse
``stage`` events while the request runs and then exactly one terminal event:
``result`` carries the same ``AcademicAskResponse`` the non-streaming route
returns, built after evidence verification has finished; ``error`` carries a
fixed, browser-safe failure. No model text is ever sent before ``result``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from aaos.ask_progress import ASK_STAGE_REPORTER, AskStage
from aaos.web.contracts import AcademicAskResponse

_LOGGER = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 15.0
_POLL_INTERVAL_SECONDS = 0.1


class _DisconnectChecker(Protocol):
    async def is_disconnected(self) -> bool: ...


def _frame(event: str, payload: object) -> bytes:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


def _stage_frame(stage: AskStage, started: float) -> bytes:
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    return _frame("stage", {"stage": stage.value, "elapsed_ms": elapsed_ms})


async def iter_ask_sse(
    request: _DisconnectChecker,
    run: Callable[[asyncio.Event], Awaitable[AcademicAskResponse]],
) -> AsyncIterator[bytes]:
    """Run one Ask request and stream its stages, then its verified result."""

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    stages: asyncio.Queue[AskStage] = asyncio.Queue()
    cancellation = asyncio.Event()
    started = time.monotonic()

    def report(stage: AskStage) -> None:
        if threading.get_ident() == loop_thread:
            stages.put_nowait(stage)
        else:
            loop.call_soon_threadsafe(stages.put_nowait, stage)

    # The task copies the current context when it is created, so the reporter
    # reaches every stage below it without changing any service signature.
    async def execute() -> AcademicAskResponse:
        return await run(cancellation)

    token = ASK_STAGE_REPORTER.set(report)
    try:
        ask_task = asyncio.create_task(execute())
    finally:
        ASK_STAGE_REPORTER.reset(token)

    try:
        yield _stage_frame(AskStage.RECEIVED, started)
        last_frame = time.monotonic()
        while not ask_task.done() or not stages.empty():
            if await request.is_disconnected():
                cancellation.set()
                return
            try:
                stage = await asyncio.wait_for(stages.get(), _POLL_INTERVAL_SECONDS)
            except TimeoutError:
                if time.monotonic() - last_frame >= _HEARTBEAT_INTERVAL_SECONDS:
                    yield b": keep-alive\n\n"
                    last_frame = time.monotonic()
                continue
            yield _stage_frame(stage, started)
            last_frame = time.monotonic()

        try:
            response = ask_task.result()
        except Exception:
            _LOGGER.error("Ask stream execution failed", extra={"event": "ask_stream_failed"})
            yield _frame(
                "error",
                {
                    "code": "ASK_FAILED",
                    "message": "AAOS could not complete this request.",
                },
            )
            return
        yield _frame("result", json.loads(response.model_dump_json()))
    finally:
        if not ask_task.done():
            # The client went away: ask the run to stop, as the
            # non-streaming route does when its caller disconnects.
            cancellation.set()

        # Observe completion without abandoning exceptions. Cooperative cancellation
        # preserves action-outcome handling in the shared orchestrator.
        def observe(task: asyncio.Task[AcademicAskResponse]) -> None:
            if not task.cancelled() and task.exception() is not None:
                _LOGGER.error("Disconnected Ask failed", extra={"event": "ask_stream_failed"})

        ask_task.add_done_callback(observe)
