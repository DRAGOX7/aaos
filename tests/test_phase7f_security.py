from datetime import datetime
from pathlib import Path
from typing import NoReturn

import pytest

import aaos.main
from aaos.web.activity import (
    ActivityCategory,
    ActivityEventCode,
    ActivityResource,
    ActivitySourceRow,
    ActivityStatus,
)
from aaos.web.activity_read import project_source_row

FRONTEND_ROOT = Path(__file__).parents[1] / "frontend"
ACTIVITY_BROWSER_FILES = (
    FRONTEND_ROOT / "lib" / "api" / "activity.ts",
    FRONTEND_ROOT / "lib" / "api" / "health.ts",
    FRONTEND_ROOT / "components" / "aaos" / "activity-stream.tsx",
    FRONTEND_ROOT / "components" / "aaos" / "activity-list-view.tsx",
    FRONTEND_ROOT / "components" / "aaos" / "system-health-view.tsx",
)


def test_activity_projection_exposes_no_raw_internal_fields() -> None:
    event = project_source_row(
        ActivitySourceRow(
            source_kind="ACTION_EVENT",
            occurred_at=datetime(2026, 9, 18),
            stable_source_id="internal-event",
            source_sequence=1,
            task_id="task-1",
            approval_id="approval-1",
            action_event_code=ActivityEventCode.UNKNOWN_OUTCOME,
            task_creation_state=None,
            task_failure_code=None,
            approval_status=None,
            operation_status=ActivityStatus.UNKNOWN_OUTCOME,
            typed_detail=None,
        )
    )

    assert event.category is ActivityCategory.INTEGRATION
    assert event.resource_type is ActivityResource.APPROVAL
    assert event.detail is None
    assert event.context is None
    assert set(event.model_dump()) == {
        "schema_version",
        "activity_id",
        "occurred_at",
        "category",
        "event_code",
        "status",
        "resource_type",
        "resource_id",
        "title",
        "detail",
        "context",
    }
    assert "internal-event" in event.activity_id
    assert "sql" not in event.model_dump_json().lower()


def test_browser_activity_health_surface_has_no_write_or_secret_authority() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in ACTIVITY_BROWSER_FILES)
    assert 'method: "GET"' in source
    for forbidden in ("POST", "PUT", "PATCH", "DELETE", "TrustedApproverContext", "password", "token", "traceback", "sqlite", "provider"):
        assert forbidden.lower() not in source.lower()
    assert "/api/v1/activity" in source
    assert "/api/v1/system-health" in source
    assert "http://" not in source
    assert "https://" not in source


def test_app_construction_is_lazy_and_does_not_start_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fail_database(*args: object, **kwargs: object) -> NoReturn:
        calls.append("database")
        raise AssertionError("database must remain lazy")

    def fail_rag(*args: object, **kwargs: object) -> NoReturn:
        calls.append("rag")
        raise AssertionError("RAG runtime must remain lazy")

    monkeypatch.setattr("aaos.persistence.database.create_database_engine", fail_database)
    monkeypatch.setattr("aaos.rag.factory.build_configured_phase4_runtime", fail_rag)
    application = aaos.main.create_app()

    assert calls == []
    assert application.router.on_startup == []


def test_activity_and_health_read_services_have_no_mutation_path() -> None:
    activity_source = (
        Path(__file__).parents[1] / "src" / "aaos" / "web" / "activity_read.py"
    ).read_text(encoding="utf-8")
    health_source = (
        Path(__file__).parents[1] / "src" / "aaos" / "web" / "health_read.py"
    ).read_text(encoding="utf-8")
    for source in (activity_source, health_source):
        lowered = source.lower()
        assert "session.add(" not in lowered
        assert "session.delete(" not in lowered
        assert "session.commit(" not in lowered
        assert "session.flush(" not in lowered
        assert "update(" not in lowered
