"""Streaming Ask: stage events while the request runs, then the verified result."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.agent.academic_orchestrator import AcademicAskResult, AcademicAskStatus
from aaos.ask_progress import ASK_STAGE_REPORTER, AskStage, report_ask_stage
from aaos.web.ask_stream import iter_ask_sse
from aaos.web.contracts import AcademicAskResponse, AcademicAskStatusView
from aaos.web.routes import create_api_v1_router


class _Courses:
    def list_courses(self) -> tuple[()]:
        return ()


class _StagedAskService:
    """Reports RAG stages the way the RAG service does, then answers."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def ask(
        self,
        question: str,
        cancel_event: asyncio.Event | None = None,
        *,
        conversation_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> AcademicAskResult:
        self.calls.append(question)
        for stage in (AskStage.SEARCHING, AskStage.WRITING, AskStage.VERIFYING):
            report_ask_stage(stage)
            await asyncio.sleep(0)
        # A stage reported from a worker thread must still reach the stream.
        await asyncio.to_thread(report_ask_stage, AskStage.VERIFYING)
        if self.fail:
            raise RuntimeError("internal detail that must not reach the browser")
        return AcademicAskResult(
            AcademicAskStatus.ANSWERED,
            answer_text="Backpropagation computes gradients layer by layer.",
            step_count=1,
            conversation_id=UUID("00000000-0000-4000-8000-000000000001"),
        )


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for frame in body.split("\n\n"):
        lines = [line for line in frame.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((name, data))
    return events


def _client(service: _StagedAskService) -> TestClient:
    application = FastAPI()
    application.include_router(create_api_v1_router(_Courses(), ask_service=service))
    return TestClient(application)


def test_stream_sends_stages_then_the_same_result_as_the_plain_route() -> None:
    service = _StagedAskService()
    request_id = "00000000-0000-4000-8000-000000000042"
    body = {"question": "  Explain backpropagation  ", "request_id": request_id}

    with _client(service) as client:
        streamed = client.post("/api/v1/ask/stream", json=body)
        plain = client.post("/api/v1/ask", json=body)

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["cache-control"] == "no-cache, no-store"
    events = _events(streamed.text)
    names = [name for name, _ in events]
    assert names[-1] == "result"
    assert names.count("result") == 1
    stages = [data["stage"] for name, data in events if name == "stage"]
    assert stages == ["RECEIVED", "SEARCHING", "WRITING", "VERIFYING", "VERIFYING"]
    assert all(
        isinstance(data["elapsed_ms"], int) and data["elapsed_ms"] >= 0
        for name, data in events
        if name == "stage"
    )
    # Stage events carry no answer text; only the terminal result does.
    assert all("answer_text" not in data for name, data in events if name == "stage")
    assert events[-1][1] == plain.json()
    assert events[-1][1]["request_id"] == request_id
    assert service.calls == ["Explain backpropagation", "Explain backpropagation"]


def test_stream_reports_a_fixed_error_instead_of_internal_detail() -> None:
    with _client(_StagedAskService(fail=True)) as client:
        streamed = client.post("/api/v1/ask/stream", json={"question": "Explain it"})

    events = _events(streamed.text)
    assert events[-1] == (
        "error",
        {"code": "ASK_FAILED", "message": "AAOS could not complete this request."},
    )
    assert "internal detail" not in streamed.text
    assert all(name != "result" for name, _ in events)


def test_stream_rejects_an_invalid_question_like_the_plain_route() -> None:
    with _client(_StagedAskService()) as client:
        response = client.post("/api/v1/ask/stream", json={"question": "   "})

    assert response.status_code == 422


def test_disconnect_cancels_the_running_request() -> None:
    class _Disconnected:
        async def is_disconnected(self) -> bool:
            return True

    seen: list[asyncio.Event] = []

    async def run(cancellation: asyncio.Event) -> AcademicAskResponse:
        seen.append(cancellation)
        await cancellation.wait()
        return AcademicAskResponse(status=AcademicAskStatusView.CANCELLED, step_count=0)

    async def scenario() -> list[bytes]:
        frames = [frame async for frame in iter_ask_sse(_Disconnected(), run)]
        await asyncio.sleep(0)
        return frames

    frames = asyncio.run(scenario())

    assert len(frames) == 1 and frames[0].startswith(b"event: stage")
    assert seen and seen[0].is_set()


def test_reporting_without_a_stream_is_a_no_op() -> None:
    assert ASK_STAGE_REPORTER.get() is None
    report_ask_stage(AskStage.SEARCHING)


def test_a_failing_reporter_never_fails_the_request() -> None:
    def broken(stage: AskStage) -> None:
        raise RuntimeError("boom")

    token = ASK_STAGE_REPORTER.set(broken)
    try:
        report_ask_stage(AskStage.WRITING)
    finally:
        ASK_STAGE_REPORTER.reset(token)


def test_no_answer_is_emitted_while_verification_is_pending() -> None:
    class Connected:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> None:
        verifying = asyncio.Event()
        release = asyncio.Event()

        async def run(cancel: asyncio.Event) -> AcademicAskResponse:
            report_ask_stage(AskStage.VERIFYING)
            verifying.set()
            await release.wait()
            return AcademicAskResponse(
                status=AcademicAskStatusView.INSUFFICIENT_EVIDENCE,
                answer_text="Not enough evidence.",
                step_count=1,
            )

        stream = iter_ask_sse(Connected(), run)
        received = await anext(stream)
        await verifying.wait()
        stage = await anext(stream)
        assert b"VERIFYING" in stage
        assert b"answer_text" not in received + stage
        assert b"Not enough evidence" not in received + stage
        release.set()
        final = await anext(stream)
        assert b"INSUFFICIENT_EVIDENCE" in final
        assert b"event: result" in final
        await stream.aclose()

    asyncio.run(scenario())
