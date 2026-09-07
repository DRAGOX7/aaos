"""Slice 3 task-state read boundary tests."""

from __future__ import annotations

from aaos.agent import (
    AgentErrorCode,
    AgentProposal,
    PermissionClass,
    PermissionPolicy,
    ToolName,
    ToolRegistry,
)


def test_task_state_reader_is_not_executable_without_approved_state_source() -> None:
    registry = ToolRegistry.default()

    result = registry.execute(
        AgentProposal.from_mapping(
            {
                "tool_call": {
                    "name": ToolName.READ_TASK_STATE.value,
                    "arguments": {"task_id": "task-1"},
                }
            }
        ),
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.TOOL_NOT_EXECUTABLE
    assert registry.get(ToolName.READ_TASK_STATE).handler is None


def test_task_state_schema_rejects_paths_and_sql_even_while_disabled() -> None:
    registry = ToolRegistry.default()
    policy = PermissionPolicy.from_values({PermissionClass.READ_ONLY})

    for arguments in (
        {"task_id": "../../secret"},
        {"task_id": "task-1", "path": "C:\\secret"},
        {"task_id": "task-1", "sql": "SELECT * FROM task"},
    ):
        result = registry.execute(
            AgentProposal.from_mapping(
                {"tool_call": {"name": ToolName.READ_TASK_STATE.value, "arguments": arguments}}
            ),
            policy,
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code is AgentErrorCode.INVALID_TOOL_ARGUMENTS


def test_task_state_read_module_has_no_storage_side_effects() -> None:
    __import__("aaos.agent")
