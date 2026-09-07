"""Safe local model lifecycle telemetry tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx2
import pytest

from aaos.config import AppSettings
from aaos.inference import ChatMessage, ChatRequest, ModelUnavailableError
from aaos.inference.lm_studio import LMStudioGateway
from aaos.inference.lm_studio_lifecycle import (
    APPROVED_QWEN_MODEL,
    LmStudioModelLifecycle,
    ModelLifecycleError,
)
from aaos.inference.telemetry import LocalInferenceTelemetryEvent
from aaos.rag.contracts import RagErrorCode, RagOperationalError
from aaos.rag.runtime import Phase4RagRuntime


@dataclass
class _Result:
    returncode: int
    stdout: str | None


class _FailingRunner:
    def __call__(self, _argv: tuple[str, ...]) -> _Result:
        raise TimeoutError("SECRET_TIMEOUT_DETAIL")


class _LifecycleRunner:
    def __init__(self) -> None:
        self.inventories = [
            [],
            [{"identifier": APPROVED_QWEN_MODEL, "modelKey": APPROVED_QWEN_MODEL}],
            [{"identifier": APPROVED_QWEN_MODEL, "modelKey": APPROVED_QWEN_MODEL}],
            [],
        ]

    def __call__(self, argv: tuple[str, ...]) -> _Result:
        if argv[1:] == ("ps", "--json"):
            return _Result(0, json.dumps(self.inventories.pop(0)))
        return _Result(0, "")


def test_lifecycle_failure_emits_safe_causal_event_and_keeps_public_error() -> None:
    events: list[LocalInferenceTelemetryEvent] = []
    lifecycle = LmStudioModelLifecycle(
        command_runner=_FailingRunner(),
        executable="lms",
        diagnostic_sink=events.append,
    )

    with pytest.raises(ModelLifecycleError):
        lifecycle.ensure_answer_model_unloaded()

    assert events
    event = next(item for item in events if item.exception_type == "TimeoutError")
    assert event.operation == "READY_CHECK"
    assert event.exception_type == "TimeoutError"
    assert event.safe_error_code == "MODEL_LIFECYCLE_ERROR"
    assert event.model_identifier == APPROVED_QWEN_MODEL
    assert event.lifecycle_state == "failed"
    assert "SECRET_TIMEOUT_DETAIL" not in repr(event)
    assert any(item.operation == "UNLOAD" for item in events)


def test_gateway_failure_emits_safe_backend_metadata_and_keeps_public_error() -> None:
    events: list[LocalInferenceTelemetryEvent] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            404,
            json={"error": {"code": "model_not_found", "message": "SECRET_BACKEND"}},
        )

    async def scenario() -> None:
        gateway = LMStudioGateway(
            AppSettings(), httpx2.MockTransport(handler), diagnostic_sink=events.append
        )
        try:
            with pytest.raises(ModelUnavailableError):
                await gateway.generate(
                    ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))
                )
        finally:
            await gateway.aclose()

    asyncio.run(scenario())

    event = next(item for item in events if item.operation == "INFERENCE_REQUEST")
    assert event.exception_type == "ModelUnavailableError"
    assert event.safe_error_code == "MODEL_UNAVAILABLE"
    assert event.http_status == 404
    assert event.backend_category == "model_not_found"
    assert "SECRET_PROMPT" not in repr(event)
    assert "SECRET_BACKEND" not in repr(event)


def test_runtime_cleanup_event_identifies_failing_closer_without_changing_error() -> None:
    events: list[LocalInferenceTelemetryEvent] = []
    runtime = object.__new__(Phase4RagRuntime)
    runtime._closed = False
    runtime._diagnostic_sink = events.append

    class _FailingCloser:
        identity = "failing-service"

        def __call__(self) -> None:
            raise RuntimeError("SECRET_CLEANUP")

    runtime._owned_closers = (_FailingCloser(),)

    with pytest.raises(RagOperationalError) as caught:
        runtime.close()

    assert caught.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    event = next(item for item in events if item.operation == "FINAL_CLEANUP")
    assert event.closer_identity == "failing-service"
    assert event.exception_type == "RuntimeError"
    assert event.cleanup_already_completed is False
    assert "SECRET_CLEANUP" not in repr(event)


def test_successful_lifecycle_emits_safe_operations_without_changing_commands() -> None:
    events: list[LocalInferenceTelemetryEvent] = []
    lifecycle = LmStudioModelLifecycle(
        command_runner=_LifecycleRunner(), executable="lms", diagnostic_sink=events.append
    )

    lifecycle.ensure_answer_model_loaded()
    lifecycle.close()
    lifecycle.close()

    assert [event.operation for event in events] == ["LOAD", "FINAL_CLEANUP", "FINAL_CLEANUP"]
    assert events[-1].cleanup_already_completed is True


def test_telemetry_sink_failure_cannot_change_lifecycle_behavior() -> None:
    def failing_sink(_event: LocalInferenceTelemetryEvent) -> None:
        raise RuntimeError("SECRET_SINK_FAILURE")

    lifecycle = LmStudioModelLifecycle(
        command_runner=_LifecycleRunner(), executable="lms", diagnostic_sink=failing_sink
    )

    lifecycle.ensure_answer_model_loaded()
    lifecycle.close()
