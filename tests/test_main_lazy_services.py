"""The API's lazy default StudyTask service must implement the agent port fully."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest

from aaos.agent.academic_orchestrator import StudyTaskPort
from aaos.main import _LazyDefaultStudyTaskService


def _port_methods() -> set[str]:
    return {
        name
        for name, member in vars(StudyTaskPort).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


def test_lazy_study_task_service_implements_every_port_method() -> None:
    """Regression: the live app lacked apply_moodle_deadline_change."""
    missing = {
        name
        for name in _port_methods()
        if not callable(getattr(_LazyDefaultStudyTaskService, name, None))
    }

    assert _port_methods() >= {"list", "get", "create", "update", "complete"}
    assert missing == set()


def test_lazy_study_task_service_delegates_moodle_deadline_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    sentinel = object()

    class RecordingDelegate:
        def apply_moodle_deadline_change(self, **kwargs: Any) -> object:
            calls.append(kwargs)
            return sentinel

    service = _LazyDefaultStudyTaskService()
    monkeypatch.setattr(service, "_ensure_delegate", lambda: RecordingDelegate())
    arguments: dict[str, Any] = {
        "request_id": UUID("00000000-0000-4000-8000-000000000001"),
        "assignment_id": 20,
        "expected_due_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "source_change_id": "change-1",
        "source_from_snapshot_id": 1,
        "source_to_snapshot_id": 2,
    }

    assert service.apply_moodle_deadline_change(**arguments) is sentinel
    assert calls == [arguments]


def test_default_api_assistant_enables_bilingual_intent_routing() -> None:
    """Phase I1: the API composition turns the deterministic router on."""
    from test_agent_academic_orchestration import (
        FakeAcademicReader,
        FakeDocumentReader,
        FakeRagService,
    )

    from aaos.config import AppSettings
    from aaos.main import _LazyDefaultAcademicAskService

    service = _LazyDefaultAcademicAskService(
        AppSettings(),
        FakeAcademicReader(),
        FakeDocumentReader(),
        FakeRagService(),
        _LazyDefaultStudyTaskService(),
        None,
        None,
        None,
    )

    assert service._ensure_assistant()._intent_routing is True


def test_default_api_assistant_receives_the_announcement_reader() -> None:
    """Phase I2: the API composition wires announcements into routed Ask."""
    from test_agent_academic_orchestration import (
        FakeAcademicReader,
        FakeDocumentReader,
        FakeRagService,
    )
    from test_agent_announcements import FakeAnnouncementReader

    from aaos.config import AppSettings
    from aaos.main import _LazyDefaultAcademicAskService, _LazyDefaultCourseReader

    reader = FakeAnnouncementReader()
    service = _LazyDefaultAcademicAskService(
        AppSettings(),
        FakeAcademicReader(),
        FakeDocumentReader(),
        FakeRagService(),
        _LazyDefaultStudyTaskService(),
        None,
        None,
        None,
        announcement_reader=reader,
    )

    assert service._ensure_assistant()._announcement_reader is reader
    assert callable(getattr(_LazyDefaultCourseReader(), "list_announcements", None))


def test_default_api_assistant_receives_a_lazy_summarizer() -> None:
    """Phase I3: the API composition wires verified summaries into routed Ask."""
    import asyncio

    from test_agent_academic_orchestration import (
        FakeAcademicReader,
        FakeDocumentReader,
        FakeRagService,
    )

    from aaos.config import AppSettings
    from aaos.main import (
        _DefaultAgentSummaryService,
        _LazyDefaultAcademicAskService,
        _LazyDefaultRagReader,
    )

    class _Summarizer:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def summarize_course(self, versions: Any, language: str, *, deadline: Any) -> str:
            self.calls.append((tuple(versions), language, deadline))
            return "summary"

    class _Runtime:
        summarizer = _Summarizer()

    class _Configured:
        runtime = _Runtime()

    reader = _LazyDefaultRagReader()
    summaries = _DefaultAgentSummaryService(reader)
    assert reader._runtime is None  # building the service stays lazy

    reader._runtime = _Configured()  # type: ignore[assignment]
    reader._delegate = object()  # type: ignore[assignment]
    result = asyncio.run(summaries.summarize_course((2,), "en", deadline=5.0))

    assert result == "summary"
    assert _Runtime.summarizer.calls == [((2,), "en", 5.0)]
    service = _LazyDefaultAcademicAskService(
        AppSettings(),
        FakeAcademicReader(),
        FakeDocumentReader(),
        FakeRagService(),
        _LazyDefaultStudyTaskService(),
        None,
        None,
        None,
        summarizer=summaries,
    )
    assert service._ensure_assistant()._summarizer is summaries


def _fake_configured_runtime(events: list[str]) -> Any:
    class _Runtime:
        def warm_up(self) -> None:
            events.append("runtime.warm_up")

    class _Configured:
        runtime = _Runtime()
        service = object()

        async def aclose(self) -> None:
            events.append("runtime.close")

    return _Configured()


def test_rag_reader_warm_up_loads_the_runtime_models_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ask latency: the first question no longer loads the embedder and NLI."""
    import asyncio

    import aaos.rag.factory as factory
    from aaos.main import _LazyDefaultRagReader

    events: list[str] = []
    monkeypatch.setattr(
        factory,
        "build_configured_phase4_runtime",
        lambda: events.append("build") or _fake_configured_runtime(events),
    )
    reader = _LazyDefaultRagReader(sessions=lambda: None)  # type: ignore[arg-type]

    async def scenario() -> None:
        await reader.start_warm_up()
        reader._ensure_delegate()
        await reader.dispose()

    asyncio.run(scenario())

    assert events == ["build", "runtime.warm_up", "runtime.close"]


def test_rag_reader_warm_up_failure_leaves_the_lazy_path_in_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import aaos.rag.factory as factory
    from aaos.main import _LazyDefaultRagReader

    def unavailable() -> Any:
        raise RuntimeError("no runtime data")

    monkeypatch.setattr(factory, "build_configured_phase4_runtime", unavailable)
    reader = _LazyDefaultRagReader(sessions=lambda: None)  # type: ignore[arg-type]

    async def scenario() -> None:
        await reader.start_warm_up()
        assert reader._warm_up_task is not None
        await reader._warm_up_task
        assert reader._runtime is None
        await reader.dispose()

    asyncio.run(scenario())


def test_only_the_served_app_warms_rag_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from aaos.main import _LazyDefaultRagReader, create_app

    started: list[str] = []

    async def record(self: Any) -> None:
        started.append("warm")

    monkeypatch.setattr(_LazyDefaultRagReader, "start_warm_up", record)
    with TestClient(create_app()):
        pass
    with TestClient(create_app(warm_rag_on_startup=True)):
        pass

    assert started == ["warm"]
