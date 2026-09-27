from __future__ import annotations

import pytest

from aaos.inference.lm_studio import _STRUCTURED_RESPONSE_FORMATS
from aaos.inference.models import ChatMessage, ChatRequest

_ACADEMIC_TOOLS = {
    "academic.list_courses",
    "academic.get_course",
    "academic.list_assignments",
    "academic.get_assignment",
    "academic.list_documents",
    "rag.answer",
}
_STUDY_TASK_READ_TOOLS = {"study_task.list", "study_task.get"}
_STUDY_TASK_WRITE_TOOLS = {
    "study_task.create",
    "study_task.update",
    "study_task.complete",
}


@pytest.mark.parametrize(
    ("schema_name", "expected_task_tools", "contextual", "task_context"),
    [
        ("study_task_read_decision", _STUDY_TASK_READ_TOOLS, False, False),
        ("study_task_read_decision_contextual", _STUDY_TASK_READ_TOOLS, True, False),
        (
            "study_task_write_decision",
            _STUDY_TASK_READ_TOOLS | _STUDY_TASK_WRITE_TOOLS,
            False,
            False,
        ),
        (
            "study_task_write_decision_contextual",
            _STUDY_TASK_READ_TOOLS | _STUDY_TASK_WRITE_TOOLS,
            True,
            False,
        ),
        (
            "study_task_write_decision_task_contextual",
            _STUDY_TASK_READ_TOOLS | _STUDY_TASK_WRITE_TOOLS,
            True,
            True,
        ),
    ],
)
def test_study_task_response_schema_matches_closed_runtime_surface(
    schema_name: str,
    expected_task_tools: set[str],
    contextual: bool,
    task_context: bool,
) -> None:
    request = ChatRequest(
        messages=(ChatMessage(role="user", content="study task"),),
        structured_output="json_schema",
        structured_output_schema=schema_name,  # type: ignore[arg-type]
    )
    response_format = _STRUCTURED_RESPONSE_FORMATS[schema_name]
    wrapper = response_format["json_schema"]
    schema = wrapper["schema"]
    branches = schema["oneOf"]
    call_branches = [
        branch
        for branch in branches
        if branch.get("properties", {}).get("action", {}).get("enum") == ["CALL_TOOL"]
    ]
    actual_tools = {branch["properties"]["tool_name"]["enum"][0] for branch in call_branches}

    assert request.structured_output_schema == schema_name
    assert wrapper["strict"] is True
    assert actual_tools == _ACADEMIC_TOOLS | expected_task_tools
    assert all(branch["additionalProperties"] is False for branch in call_branches)
    assert all(
        branch["properties"]["arguments"]["additionalProperties"] is False
        for branch in call_branches
    )
    for branch in call_branches:
        tool_name = branch["properties"]["tool_name"]["enum"][0]
        expects_context = contextual and not (task_context and tool_name == "study_task.create")
        assert ("context_ref" in branch["properties"]["arguments"]["properties"]) is expects_context
        assert ("context_ref" in branch["properties"]["arguments"]["required"]) is expects_context
