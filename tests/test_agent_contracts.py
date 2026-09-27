"""Slice 1 contract tests for the Phase 5 trusted boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from math import inf, nan

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentFailure,
    AgentLimits,
    AgentProposal,
    AgentState,
    AgentStep,
    AgentTask,
    AgentTaskId,
    GroundedRagResponse,
    PermissionClass,
    PermissionDecision,
    TerminalResultKind,
    ToolArguments,
    ToolCall,
    ToolError,
    ToolName,
    ToolObservation,
    ToolResult,
    canonical_json,
    fingerprint_contract,
)


def test_agent_task_starts_in_created_state_and_is_immutable() -> None:
    task = AgentTask(
        task_id=AgentTaskId("task-001"),
        request_text="Find the next deadline",
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )

    assert task.state is AgentState.CREATED
    assert task.task_id.value == "task-001"
    assert not hasattr(task, "__dict__")
    with pytest.raises(FrozenInstanceError):
        task.state = AgentState.FAILED  # type: ignore[misc]


@pytest.mark.parametrize("value", ["", " ", "../task", "task/name", "task\\name", "task id"])
def test_agent_task_id_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError, match="task_id"):
        AgentTaskId(value)


def test_agent_task_rejects_non_created_authoritative_state() -> None:
    with pytest.raises(ValueError, match="state"):
        AgentTask(AgentTaskId("task-001"), "request", state=AgentState.COMPLETED)


def test_limits_have_approved_finite_defaults() -> None:
    limits = AgentLimits()

    assert limits.max_agent_steps == 8
    assert limits.max_structured_proposal_repairs == 1
    assert limits.max_identical_tool_call_repetitions == 2
    assert limits.model_proposal_timeout_seconds == 60
    assert limits.rag_query_timeout_seconds == 120
    assert limits.read_task_state_timeout_seconds == 5
    assert limits.write_task_state_timeout_seconds == 5
    assert limits.complete_task_timeout_seconds == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_agent_steps", 0),
        ("max_structured_proposal_repairs", 0),
        ("max_structured_proposal_repairs", -1),
        ("max_identical_tool_call_repetitions", 0),
        ("model_proposal_timeout_seconds", inf),
        ("rag_query_timeout_seconds", nan),
        ("complete_task_timeout_seconds", True),
    ],
)
def test_limits_reject_non_positive_or_non_finite_values(field: str, value: object) -> None:
    values = {field: value}
    with pytest.raises(ValueError, match=field):
        AgentLimits(**values)  # type: ignore[arg-type]


def test_tool_name_is_a_closed_logical_allowlist() -> None:
    assert {member.value for member in ToolName} == {
        "rag_query",
        "academic.list_courses",
        "academic.get_course",
        "academic.list_assignments",
        "academic.get_assignment",
        "academic.list_documents",
        "moodle.list_changes",
        "rag.answer",
        "study_task.list",
        "study_task.get",
        "study_task.create",
        "study_task.update",
        "study_task.complete",
        "calendar.propose_create_event",
        "read_task_state",
        "write_task_state",
        "complete_task",
    }
    with pytest.raises(ValueError):
        ToolName("python -c 'bad'")
    with pytest.raises(ValueError):
        ToolName("C:/secret")


def test_raw_tool_arguments_are_explicitly_unvalidated() -> None:
    arguments = ToolArguments.from_mapping({"question": "What is due?", "count": 2})

    assert arguments.validated is False
    assert arguments.to_mapping() == {"question": "What is due?", "count": 2}
    with pytest.raises(ValueError, match="validated"):
        ToolArguments((("question", "What is due?"),), validated=True)


def test_tool_call_cannot_accept_a_plain_mapping_as_executable_arguments() -> None:
    with pytest.raises(TypeError, match="ToolArguments"):
        ToolCall(ToolName.RAG_QUERY, {"question": "unsafe"})  # type: ignore[arg-type]


def test_proposal_requires_exactly_one_untrusted_action() -> None:
    call = ToolCall(ToolName.RAG_QUERY, ToolArguments.from_mapping({"question": "q"}))
    assert AgentProposal(tool_call=call).tool_call is call

    with pytest.raises(ValueError, match="exactly one"):
        AgentProposal()
    with pytest.raises(ValueError, match="exactly one"):
        AgentProposal(tool_call=call, terminal_text="also complete")


def test_proposal_parser_rejects_unknown_and_trusted_field_injection() -> None:
    with pytest.raises(ValueError, match="unknown"):
        AgentProposal.from_mapping({"state": "COMPLETED", "terminal_text": "done"})
    with pytest.raises(ValueError, match="unknown"):
        AgentProposal.from_mapping({"permission": "TERMINAL", "terminal_text": "done"})


def test_proposal_parser_builds_only_untrusted_tool_call() -> None:
    proposal = AgentProposal.from_mapping(
        {
            "proposal_id": "proposal-1",
            "tool_call": {"name": "rag_query", "arguments": {"question": "q"}},
        }
    )

    assert proposal.tool_call is not None
    assert proposal.tool_call.name is ToolName.RAG_QUERY
    assert proposal.tool_call.arguments.validated is False


def test_errors_results_and_observations_are_safe_typed_values() -> None:
    error = ToolError(AgentErrorCode.PERMISSION_DENIED, "Permission denied", retryable=False)
    result = ToolResult.failure(error)
    observation = ToolObservation(
        kind="error", status="denied", fields=(("code", error.code.value),)
    )
    failure = AgentFailure(AgentErrorCode.MODEL_TIMEOUT, "Model timed out", stage="planning")
    decision = PermissionDecision(
        allowed=False,
        required_permission=PermissionClass.READ_ONLY,
        reason_code=AgentErrorCode.PERMISSION_DENIED,
        policy_version="v1",
    )

    assert result.ok is False
    assert result.error == error
    assert observation.fields == (("code", "PERMISSION_DENIED"),)
    assert failure.retryable is False
    assert decision.allowed is False
    with pytest.raises(TypeError):
        ToolResult.success(Exception("not serializable"))


def test_terminal_result_and_step_validate_trusted_shapes() -> None:
    failure = AgentFailure(AgentErrorCode.TASK_CANCELLED, "Cancelled", stage="orchestrator")
    from aaos.agent.contracts import AgentTerminalResult

    terminal = AgentTerminalResult(
        task_id=AgentTaskId("task-001"),
        state=AgentState.FAILED,
        step_count=1,
        failure=failure,
    )
    step = AgentStep(
        step_number=1,
        from_state=AgentState.CREATED,
        to_state=AgentState.PLANNING,
        proposal_fingerprint="a" * 64,
    )

    assert terminal.state is AgentState.FAILED
    assert step.step_number == 1
    with pytest.raises(ValueError, match="terminal"):
        AgentTerminalResult(AgentTaskId("task-001"), AgentState.PLANNING, 0)
    with pytest.raises(ValueError, match="fingerprint"):
        AgentStep(1, AgentState.CREATED, AgentState.PLANNING, "not-a-hash")


def test_grounded_terminal_contract_rejects_replacement_text_and_preserves_lineage() -> None:
    payload = {
        "answer_text": "Trusted answer",
        "citations": (
            {
                "claim_id": "claim-1",
                "document_version_id": 7,
                "document_title": "source.pdf",
                "page_start": 1,
                "page_end": 1,
            },
        ),
        "content_revision": 2,
        "generation_id": 3,
        "language": "en",
        "rendered_text": "Trusted answer\n[source.pdf, page 1]",
        "status": "ANSWER",
    }
    trusted = GroundedRagResponse(AgentTaskId("task-001"), "rag-ref", payload)
    from aaos.agent.contracts import AgentTerminalResult

    result = AgentTerminalResult(
        AgentTaskId("task-001"),
        AgentState.COMPLETED,
        1,
        result_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
        grounded_rag_response=trusted,
    )

    assert result.grounded is True
    assert result.result_text == payload["rendered_text"]
    with pytest.raises(ValueError, match="preserve"):
        AgentTerminalResult(
            AgentTaskId("task-001"),
            AgentState.COMPLETED,
            1,
            result_text="forged",
            result_kind=TerminalResultKind.GROUNDED_RAG_RESPONSE,
            grounded_rag_response=trusted,
        )


def test_contract_serialization_is_deterministic_and_json_compatible() -> None:
    left = ToolArguments.from_mapping({"b": 2, "a": "x"})
    right = ToolArguments.from_mapping({"a": "x", "b": 2})

    assert canonical_json(left) == canonical_json(right)
    assert fingerprint_contract(left) == fingerprint_contract(right)
    assert "Exception" not in canonical_json(left)


@pytest.mark.parametrize("module_name", ["aaos.agent", "aaos.agent.contracts", "aaos.agent.state"])
def test_agent_import_surface_is_side_effect_free(module_name: str) -> None:
    __import__(module_name)
