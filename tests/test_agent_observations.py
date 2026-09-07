"""Slice 6 tests for the trusted observation sanitization boundary."""

from __future__ import annotations

import json

import pytest

from aaos.agent.contracts import AgentErrorCode, ToolError, ToolName, ToolResult
from aaos.agent.observations import (
    MAX_OBSERVATION_SIZE,
    ObservationSanitizationError,
    ObservationSanitizer,
    sanitize_tool_result,
)


def test_allowlisted_scalar_fields_survive_with_unknown_fields_removed() -> None:
    result = ToolResult.success(
        {
            "task_id": "task-001",
            "state": "READY",
            "summary": "A short safe summary.",
            "handler": "do_not_expose",
            "permissions": ["LOCAL_STATE_WRITE"],
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    assert dict(observation.fields) == {
        "output_fingerprint": dict(observation.fields)["output_fingerprint"],
        "state": "READY",
        "summary": "A short safe summary.",
        "task_id": "task-001",
    }
    assert "handler" not in dict(observation.fields)
    assert "permissions" not in dict(observation.fields)


@pytest.mark.parametrize(
    "secret_text",
    [
        "api_key=sk-test-123456",
        "Authorization: Bearer abc.def.ghi",
        "X-API-Key: x-api-key-secret",
        "cookie: session=secret-cookie",
        "oauth_token=oauth-secret",
        "token=standalone-secret",
        "password=super-secret",
        "Moodle credentials: user@example.test / secret",
    ],
)
def test_secret_and_auth_material_is_not_exposed(secret_text: str) -> None:
    result = ToolResult.success({"summary": secret_text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert secret_text not in serialized
    assert "sk-test-123456" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "secret-cookie" not in serialized


@pytest.mark.parametrize(
    "path_text",
    [
        r"C:\Users\aljaf\data\credentials\token.json",
        "/home/aljaf/.config/aaos/credentials.json",
        r"C:\Users\aljaf\AppData\Local\Temp\aaos-phase4\secret.txt",
    ],
)
def test_raw_filesystem_paths_are_redacted(path_text: str) -> None:
    observation = sanitize_tool_result(
        ToolResult.success({"summary": f"location={path_text}", "task_id": "task-001"}),
        tool_name=ToolName.READ_TASK_STATE,
    )

    assert path_text not in json.dumps(observation.to_dict(), ensure_ascii=False)
    summary = dict(observation.fields)["summary"]
    assert isinstance(summary, str)
    assert "[REDACTED_PATH]" in summary


def test_sql_query_and_internal_error_data_do_not_cross_boundary() -> None:
    result = ToolResult.success(
        {
            "summary": (
                "SELECT password FROM credentials; "
                "Traceback (most recent call last): ValueError('/secret/db.sqlite3')"
            ),
            "task_id": "task-001",
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)
    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)

    assert "SELECT password" not in serialized
    assert "Traceback" not in serialized
    assert "ValueError" not in serialized
    assert "credentials" not in serialized


def test_tool_failure_exposes_only_safe_error_metadata() -> None:
    result = ToolResult.failure(
        ToolError(
            AgentErrorCode.HANDLER_EXECUTION_FAILED,
            "Traceback: Authorization: Bearer secret at C:\\credentials\\db.sqlite3",
        )
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    assert observation.status == "ERROR"
    assert observation.text == "tool execution failed"
    assert dict(observation.fields) == {
        "error_code": AgentErrorCode.HANDLER_EXECUTION_FAILED.value,
        "retryable": False,
    }


def test_rag_projection_preserves_safe_phase4_fields_but_bounds_text() -> None:
    result = ToolResult.success(
        {
            "answer_text": "The answer is supported.",
            "citations": [],
            "content_revision": 1,
            "generation_id": 2,
            "language": "en",
            "rendered_text": "The answer is supported.",
            "status": "ANSWER",
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.RAG_QUERY)

    assert observation.kind == "rag_query"
    assert observation.text == "The answer is supported."
    assert dict(observation.fields)["status"] == "ANSWER"
    assert len(json.dumps(observation.to_dict(), ensure_ascii=False)) <= MAX_OBSERVATION_SIZE


def test_oversized_generic_text_is_deterministically_truncated() -> None:
    text = "safe " * 10_000
    observation = sanitize_tool_result(
        ToolResult.success({"summary": text, "task_id": "task-001"}),
        tool_name=ToolName.READ_TASK_STATE,
    )

    summary = dict(observation.fields)["summary"]
    assert isinstance(summary, str)
    assert len(summary) < len(text)
    assert summary.endswith("[TRUNCATED]")
    assert len(json.dumps(observation.to_dict(), ensure_ascii=False)) <= MAX_OBSERVATION_SIZE


def test_sanitization_failure_is_fail_closed_without_raw_result_fallback() -> None:
    sanitizer = ObservationSanitizer(max_size=128, max_text_size=64)
    result = ToolResult.success({"summary": "safe" * 100, "task_id": "task-001"})

    with pytest.raises(ObservationSanitizationError) as exc_info:
        sanitizer.sanitize(result, tool_name=ToolName.READ_TASK_STATE)

    assert exc_info.value.code is AgentErrorCode.OBSERVATION_SANITIZATION_FAILED
    assert "safe" * 100 not in str(exc_info.value)


def test_fingerprint_and_output_are_stable_for_same_result() -> None:
    result = ToolResult.success({"summary": "stable", "task_id": "task-001"})
    first = ObservationSanitizer().sanitize_with_audit(result, tool_name=ToolName.READ_TASK_STATE)
    second = ObservationSanitizer().sanitize_with_audit(result, tool_name=ToolName.READ_TASK_STATE)

    assert first.observation == second.observation
    assert first.audit == second.audit


def test_import_boundary_has_no_execution_side_effects() -> None:
    assert callable(sanitize_tool_result)
