"""Slice 6 tests for the trusted observation sanitization boundary."""

from __future__ import annotations

import hashlib
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
        "client_secret=client-secret-value",
        "private_key=private-key-value",
        '{"secret_key":"sk-live-secret"}',
        '{"api_secret":"sk-live-secret"}',
        '{"db_password":"db-live-secret"}',
        "signing_key=signing-secret-value",
        '{"signing_key":"signing-secret-value"}',
        '{"api_key":"sk-live-secret"}',
        '{"password": "super-secret"}',
        '{"X-API-Key":"sk-live-secret"}',
        '{"X-Auth-Token":"private-token"}',
        '{"password":"prefix\\"sk-live-secret"}',
        '{"client_secret":"client-secret-value"}',
        '{"private_key":"private-key-value"}',
        '{"password":"prefix\nsk-live-secret"}',
        '{"api.key":"sk-live-secret"}',
        "encryption_key=abc123",
        "--api-key abc123",
        "Authorization Bearer abc123",
        "-----BEGIN PRIVATE KEY-----\\nprivate material\\n-----END PRIVATE KEY-----",
        '{"exception":"private backend detail"}',
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
        r"C:\Users\Jane Doe\data\credentials\token.json",
        "/home/aljaf/.config/aaos/credentials.json",
        "/home/Jane Doe/.config/aaos/credentials.json",
        "data/credentials/token.json",
        "../data/credentials/token.json",
        "data/credentials/token",
        "../data/credentials/token",
        "auth/state",
        "database/aaos",
        "src/config",
        "repo.v1/credentials/db.sqlite3",
        r"C:\Users\aljaf\AppData\Local\Temp\aaos-phase4\secret.txt",
        "users/aljaf",
        "//server/share/credentials/db.sqlite3",
        "//files.example.com/share/credentials/db.sqlite3",
        "//files.example.com/course-materials/credentials/db.sqlite3",
        "//files.example.com/course-materials/credentials.db",
        "file://server/share/credentials/db.sqlite3",
        "~/credentials/token.json",
        "$HOME/data/credentials/token.json",
        "$PWD/data/credentials/token",
        "%USERPROFILE%\\data\\credentials\\token.json",
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            r"C:\Users\Jane Doe\secret.txt then review chapter 4",
            "[REDACTED_PATH] then review chapter 4",
        ),
        (
            "/home/Jane Doe/private.txt then preserve this course guidance",
            "[REDACTED_PATH] then preserve this course guidance",
        ),
        (
            "Use data/science notes for the assignment.",
            "Use data/science notes for the assignment.",
        ),
        (
            "The ratio was 1/2.",
            "The ratio was 1/2.",
        ),
        (
            "The date is 2026/09/08.",
            "The date is 2026/09/08.",
        ),
        (
            "Use input/output labels in the table.",
            "Use input/output labels in the table.",
        ),
        (
            "The answer is yes/no.",
            "The answer is yes/no.",
        ),
        (
            "See data/science.",
            "See data/science.",
        ),
        (
            "The database contains documents for the RAG lesson.",
            "The database contains documents for the RAG lesson.",
        ),
        (
            "Use src/aaos/agent/observations.py for implementation notes.",
            "Use [REDACTED_PATH] for implementation notes.",
        ),
        (
            "docs/my_notes.txt",
            "[REDACTED_PATH]",
        ),
        (
            "C:\\Users\\Jane Doe\\folder\\",
            "[REDACTED_PATH]",
        ),
        (
            "https://example.com/course/notes for details.",
            "https://example.com/course/notes for details.",
        ),
        (
            "https://example.com/course/notes.pdf for details.",
            "https://example.com/course/notes.pdf for details.",
        ),
        (
            "https://example.com/course/module/lesson for details.",
            "https://example.com/course/module/lesson for details.",
        ),
        (
            "file:///home/aljaf/course-notes",
            "[REDACTED_PATH]",
        ),
        (
            "file:///home/aljaf/notes.pdf",
            "[REDACTED_PATH]",
        ),
        (
            "Use file:///home/aljaf/notes.pdf for details; SELECT course.",
            "Use [REDACTED_PATH] for details; SELECT course.",
        ),
        (
            "See //files.example.com/course-materials/credentials/db.sqlite3 then review chapter 4.",
            "See [REDACTED_PATH] then review chapter 4.",
        ),
        (
            "repo.v1.2/credentials/db.sqlite3 then preserve this prose.",
            "[REDACTED_PATH] then preserve this prose.",
        ),
        (
            "foo.bar/John Doe/private/key.txt for details.",
            "[REDACTED_PATH] for details.",
        ),
        (
            "/home/Jane Doe/private data/course.txt for details.",
            "[REDACTED_PATH] for details.",
        ),
        (
            "private data/course.txt for details.",
            "[REDACTED_PATH] for details.",
        ),
        (
            "src/aaos/agent/",
            "[REDACTED_PATH]",
        ),
        (
            "src/aaos/agent/observations",
            "[REDACTED_PATH]",
        ),
    ],
)
def test_path_redaction_stops_at_boundaries_without_corrupting_prose(
    text: str, expected: str
) -> None:
    result = ToolResult.success(
        {"summary": text, "task_id": "task-001"},
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    assert dict(observation.fields)["summary"] == expected


@pytest.mark.parametrize(
    "text",
    [
        r"C:\Users\Jane Doe\folder name",
        r"C:\Users\Jane Doe\folder name, then preserve this prose",
        r"C:\Users\Jane Doe\folder name then review chapter 4.",
        "C:\\Users\\Jane Doe\\folder name\npreserve this prose",
        '"C:\\Users\\Jane Doe\\folder name"',
        r"Open C:\Users\Jane Doe\course materials to prepare chapter 4.",
        r"C:\Users\Jane Doe\folder name contains the notes.",
        r"C:\Users\Jane Doe\folder name هذه جملة عربية",
        "file://server/share/John Doe/course notes for details.",
        "//server/share/John Doe/course notes for details.",
        "~/John Doe/course notes for details.",
        ".config/John Doe/course notes for details.",
        "docs/my notes for details.",
        r"C:\Users\Jane Doe\private data\course notes for details.",
        "/home/Jane Doe/private data/course notes for details.",
        "foo.bar/John Doe/private/key notes for details.",
        "private data/course notes for details.",
    ],
)
def test_ambiguous_extensionless_paths_fail_closed_without_rewriting_prose(
    text: str,
) -> None:
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    with pytest.raises(ObservationSanitizationError):
        sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)


def test_operational_sql_field_and_internal_error_data_do_not_cross_boundary() -> None:
    result = ToolResult.success(
        {
            "summary": "A normal academic sentence about SQL remains readable.",
            "query": "SELECT password FROM credentials;",
            "trace": "Traceback (most recent call last): ValueError('/secret/db.sqlite3')",
            "task_id": "task-001",
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)
    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)

    assert "A normal academic sentence about SQL remains readable." in serialized
    assert "SELECT password" not in serialized
    assert "Traceback" not in serialized
    assert "ValueError" not in serialized
    assert "credentials" not in serialized


def test_structured_exception_text_does_not_cross_boundary() -> None:
    result = ToolResult.success(
        {"summary": '{"error":"ValueError: internal lookup failed"}', "task_id": "task-001"}
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "ValueError" not in serialized
    assert "internal lookup failed" not in serialized


@pytest.mark.parametrize(
    "text",
    [
        'ValueError("first line\\nsecond private detail")',
        "ValueError: first line\nsecond private detail",
        '{"error":{"type":"ValueError","message":"private detail"}}',
        '{"exception_type":"ValueError","message":"private backend detail"}',
        '{"exception_class":"ValueError","message":"private backend detail"}',
        '{"error_type":"RuntimeError","detail":"private backend detail"}',
        '{"error_class":"RuntimeError","detail":"private backend detail"}',
    ],
)
def test_multiline_and_nested_exception_data_do_not_cross_boundary(text: str) -> None:
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "ValueError" not in serialized
    assert "private detail" not in serialized
    assert "private backend detail" not in serialized
    assert "second private detail" not in serialized


@pytest.mark.parametrize(
    "text",
    [
        '{"error":"ValueError: internal lookup failed, secret detail"}',
        '{"error":"ValueError: internal lookup failed\\nsecret detail"}',
        '{"error":"ValueError: internal lookup failed\nsecret detail"}',
        "{'exception':'RuntimeError: private backend detail'}",
    ],
)
def test_structured_exception_suffix_and_multiline_text_do_not_cross_boundary(text: str) -> None:
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "ValueError" not in serialized
    assert "RuntimeError" not in serialized
    assert "internal lookup failed" not in serialized
    assert "private backend detail" not in serialized
    assert "secret detail" not in serialized


@pytest.mark.parametrize(
    "text",
    [
        'Backend returned ValueError: "private backend detail" while reading.',
        '{"message":"Error: private detail"}',
        '{"message":"Exception: private detail"}',
        '{"error":"ValueError: internal lookup failed\nsecret detail"}',
    ],
)
def test_embedded_exception_messages_do_not_cross_boundary(text: str) -> None:
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "ValueError" not in serialized
    assert "Error:" not in serialized
    assert "Exception:" not in serialized
    assert "private backend detail" not in serialized
    assert "private detail" not in serialized
    assert "secret detail" not in serialized


def test_url_credentials_do_not_cross_boundary() -> None:
    text = "See https://alice:supersecret@example.com/private for details."
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "alice" not in serialized
    assert "supersecret" not in serialized
    assert "https://[REDACTED_AUTH]@" in serialized


def test_url_credentials_with_at_sign_do_not_cross_boundary() -> None:
    text = "See https://alice:p@ssword@example.com/private."
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "alice" not in serialized
    assert "p@ssword" not in serialized


@pytest.mark.parametrize(
    "text",
    [
        "postgresql://alice:supersecret@db.internal/prod",
        "ssh://alice:supersecret@host.internal/private",
        "redis://alice:supersecret@cache.internal/0",
    ],
)
def test_non_http_url_credentials_do_not_cross_boundary(text: str) -> None:
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "alice" not in serialized
    assert "supersecret" not in serialized


def test_protocol_relative_url_credentials_do_not_cross_boundary() -> None:
    text = "See //alice:supersecret@example.com/course/notes."
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "alice" not in serialized
    assert "supersecret" not in serialized


def test_empty_user_url_credentials_do_not_cross_boundary() -> None:
    text = "Use redis://:supersecret@cache.internal/0 for the lookup."
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert "supersecret" not in serialized


def test_protocol_relative_web_url_is_not_treated_as_unc_path() -> None:
    text = "See //example.com/course/notes for details."
    result = ToolResult.success({"summary": text, "task_id": "task-001"})

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    assert dict(observation.fields)["summary"] == text


def test_rag_answer_preserves_sql_keyword_prose_without_rewriting() -> None:
    sentence = (
        "Select the correct course, create a study plan, update it, drop obsolete notes, "
        "and insert the final citation."
    )
    result = ToolResult.success(
        {
            "answer_text": sentence,
            "citations": [],
            "content_revision": 1,
            "generation_id": 2,
            "language": "en",
            "rendered_text": sentence,
            "status": "ANSWER",
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.RAG_QUERY)

    assert observation.text == sentence
    assert "[REDACTED_QUERY]" not in json.dumps(observation.to_dict(), ensure_ascii=False)


def test_rag_answer_redacts_operational_paths_without_sql_keyword_rewriting() -> None:
    path = r"C:\Users\aljaf\data\credentials\token.json"
    sentence = f"Select the course notes stored at {path}."
    result = ToolResult.success(
        {
            "answer_text": sentence,
            "citations": [],
            "content_revision": 1,
            "generation_id": 2,
            "language": "en",
            "rendered_text": sentence,
            "status": "ANSWER",
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.RAG_QUERY)

    assert path not in str(observation.to_dict())
    assert observation.text == "Select the course notes stored at [REDACTED_PATH]."


def test_operational_path_field_is_excluded_instead_of_rewriting_prose() -> None:
    path = r"C:\Users\aljaf\data\credentials\token.json"
    result = ToolResult.success(
        {
            "task_id": "task-001",
            "summary": "A safe state summary.",
            "path": path,
        }
    )

    observation = sanitize_tool_result(result, tool_name=ToolName.READ_TASK_STATE)

    serialized = json.dumps(observation.to_dict(), ensure_ascii=False)
    assert path not in serialized
    assert "path" not in dict(observation.fields)


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


def test_stale_context_failure_has_a_safe_closed_message() -> None:
    result = ToolResult.failure(
        ToolError(AgentErrorCode.STALE_CONTEXT, "database details must stay private")
    )

    observation = sanitize_tool_result(
        result, tool_name=ToolName.STUDY_TASK_ACKNOWLEDGE_DEADLINE_CHANGE
    )

    assert observation.status == "ERROR"
    assert observation.text == "trusted context is stale; no action was applied"
    assert "database details" not in observation.text
    assert dict(observation.fields) == {
        "error_code": AgentErrorCode.STALE_CONTEXT.value,
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
    sanitizer = ObservationSanitizer(max_size=64, max_text_size=32)
    result = ToolResult.success({"summary": "safe" * 100, "task_id": "task-001"})

    with pytest.raises(ObservationSanitizationError) as exc_info:
        sanitizer.sanitize(result, tool_name=ToolName.READ_TASK_STATE)

    assert exc_info.value.code is AgentErrorCode.OBSERVATION_SANITIZATION_FAILED
    assert "safe" * 100 not in str(exc_info.value)


def test_observation_and_audit_metadata_are_stable_for_same_result() -> None:
    result = ToolResult.success({"summary": "stable", "task_id": "task-001"})
    first = ObservationSanitizer().sanitize_with_audit(result, tool_name=ToolName.READ_TASK_STATE)
    second = ObservationSanitizer().sanitize_with_audit(result, tool_name=ToolName.READ_TASK_STATE)

    assert first.observation == second.observation
    assert first.audit == second.audit


def test_private_raw_payload_fingerprint_is_not_retained_or_exposed() -> None:
    result = ToolResult.success({"summary": "low entropy private value", "task_id": "task-001"})
    raw_fingerprint = hashlib.sha256(
        json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    sanitized = ObservationSanitizer().sanitize_with_audit(
        result, tool_name=ToolName.READ_TASK_STATE
    )

    observation_json = json.dumps(sanitized.observation.to_dict(), ensure_ascii=False)
    assert raw_fingerprint not in observation_json
    assert "output_fingerprint" not in observation_json
    audit_json = json.dumps(sanitized.audit.to_dict(), ensure_ascii=False)
    assert raw_fingerprint not in audit_json
    assert "source_fingerprint" not in audit_json


def test_import_boundary_has_no_execution_side_effects() -> None:
    assert callable(sanitize_tool_result)
