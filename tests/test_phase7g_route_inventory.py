from __future__ import annotations

from aaos.main import create_app

EXPECTED_API_PATHS = {
    "/api/v1/ask",
    "/api/v1/ask/stream",
    "/api/v1/courses",
    "/api/v1/courses/{course_id}",
    "/api/v1/assignments",
    "/api/v1/documents",
    "/api/v1/documents/{document_id}",
    "/api/v1/rag/queries",
    "/api/v1/tasks",
    "/api/v1/tasks/{task_id}",
    "/api/v1/approvals",
    "/api/v1/approvals/{approval_id}",
    "/api/v1/approvals/{approval_id}/approve",
    "/api/v1/approvals/{approval_id}/reject",
    "/api/v1/activity",
    "/api/v1/activity/stream",
    "/api/v1/system-health",
    # Read-only: local study tasks, and a document's original file.
    "/api/v1/study-tasks",
    "/api/v1/documents/{document_id}/file",
    "/api/v1/document-versions/{version_id}/evidence",
    "/api/v1/document-versions/{version_id}/file",
    # The answering model: which one is running, and the confirmed local/Gemini switch.
    "/api/v1/model",
    "/api/v1/model/gemini-models",
    "/api/v1/model/chatgpt-plan-models",
}


def test_phase7_api_path_inventory_is_exact() -> None:
    paths = set(create_app().openapi()["paths"])

    assert {path for path in paths if path.startswith("/api/v1/")} == EXPECTED_API_PATHS
    assert "/settings" not in paths
    assert "/health" in paths


def test_phase7_mutation_inventory_is_closed() -> None:
    paths = create_app().openapi()["paths"]
    mutation_paths = {
        (path, method.upper())
        for path, operations in paths.items()
        for method in operations
        if method.lower() in {"post", "put", "patch", "delete"}
    }

    assert mutation_paths == {
        ("/api/v1/rag/queries", "POST"),
        ("/api/v1/ask", "POST"),
        ("/api/v1/ask/stream", "POST"),
        ("/api/v1/approvals/{approval_id}/approve", "POST"),
        ("/api/v1/approvals/{approval_id}/reject", "POST"),
        # Changes where questions are processed; fenced to the local page, needs confirmation
        # for the cloud model, and never carries a key.
        ("/api/v1/model", "POST"),
    }
