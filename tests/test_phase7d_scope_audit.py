from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phase7d_sources_do_not_add_future_mutation_routes_or_sse() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "src/aaos/web/contracts.py",
            ROOT / "src/aaos/web/rag.py",
            ROOT / "src/aaos/web/routes.py",
            ROOT / "frontend/components/aaos/ask-view.tsx",
        )
    ).lower()

    assert "approv" not in source_text
    assert "calendar" not in source_text
    assert "eventsource" not in source_text
    assert "sse" not in source_text
    assert "put(" not in source_text
    assert "delete(" not in source_text
