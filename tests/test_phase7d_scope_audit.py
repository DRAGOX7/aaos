from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


_ROUTE_DECORATOR = re.compile(
    r'@router\.(get|post|put|patch|delete)\(\s*"([^"]+)"',
    re.IGNORECASE,
)


def test_phase7d_boundary_remains_closed_after_later_phase_additions() -> None:
    rag_route_text = (ROOT / "src/aaos/web/rag.py").read_text(encoding="utf-8").lower()
    ask_view_text = (
        (ROOT / "frontend/components/aaos/ask-view.tsx").read_text(encoding="utf-8").lower()
    )
    phase7d_source_text = rag_route_text + "\n" + ask_view_text

    assert "approv" not in phase7d_source_text
    assert "calendar" not in rag_route_text
    # Phase H's accepted unified Ask page describes Calendar proposals in its
    # copy, but the Phase 7D view must still not import or call Calendar code.
    assert not re.search(r"^\s*import\b[^\n]*calendar", ask_view_text, re.MULTILINE)
    assert "/calendar" not in ask_view_text
    assert "eventsource" not in phase7d_source_text
    assert "sse" not in phase7d_source_text
    assert "put(" not in phase7d_source_text
    assert "delete(" not in phase7d_source_text

    current_routes = (ROOT / "src/aaos/web/routes.py").read_text(encoding="utf-8")
    observed_routes = {
        (method.upper(), path) for method, path in _ROUTE_DECORATOR.findall(current_routes)
    }
    assert observed_routes == {
        ("GET", "/activity"),
        ("GET", "/activity/stream"),
        ("GET", "/system-health"),
        ("GET", "/courses"),
        ("GET", "/courses/{course_id}"),
        ("GET", "/assignments"),
        ("GET", "/documents"),
        ("GET", "/documents/{document_id}"),
        ("GET", "/documents/{document_id}/file"),
        ("GET", "/study-tasks"),
        ("GET", "/model"),
        ("GET", "/model/gemini-models"),
        ("GET", "/model/chatgpt-plan-models"),
        ("POST", "/model"),
        ("POST", "/ask"),
        ("POST", "/rag/queries"),
        ("GET", "/tasks"),
        ("GET", "/tasks/{task_id}"),
        ("GET", "/approvals"),
        ("GET", "/approvals/{approval_id}"),
        ("POST", "/approvals/{approval_id}/approve"),
        ("POST", "/approvals/{approval_id}/reject"),
    }
