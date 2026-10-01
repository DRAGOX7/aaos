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
