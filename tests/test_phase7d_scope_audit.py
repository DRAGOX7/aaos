from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase7d_sources_do_not_add_future_mutation_routes_or_sse() -> None:
    phase7d_source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "src/aaos/web/rag.py",
            ROOT / "frontend/components/aaos/ask-view.tsx",
        )
    ).lower()

    assert "approv" not in phase7d_source_text
    assert "calendar" not in phase7d_source_text
    assert "eventsource" not in phase7d_source_text
    assert "sse" not in phase7d_source_text
    assert "put(" not in phase7d_source_text
    assert "delete(" not in phase7d_source_text

    current_routes = (ROOT / "src/aaos/web/routes.py").read_text(encoding="utf-8").lower()
    assert current_routes.count("/approvals/{approval_id}/approve") == 1
    assert current_routes.count("/approvals/{approval_id}/reject") == 1
    for forbidden_route in (
        "/execute",
        "/provider",
        "/external",
        "/activity",
        "/system-health",
        "eventsource",
        "sse",
    ):
        assert forbidden_route not in current_routes
