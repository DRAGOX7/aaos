from __future__ import annotations

from fastapi.testclient import TestClient

from aaos.main import create_app


def test_app_startup_and_health_do_not_build_rag_runtime(monkeypatch) -> None:
    import aaos.rag.factory

    def fail_if_built(*args, **kwargs):
        raise AssertionError("RAG runtime must be lazy")

    monkeypatch.setattr(aaos.rag.factory, "build_configured_phase4_runtime", fail_if_built)

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
