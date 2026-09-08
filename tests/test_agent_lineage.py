"""C2 trusted final-output lineage tests.

These tests deliberately exercise the boundary between a trusted Phase 4
tool result and an untrusted model terminal proposal.  The model may select a
task-local result reference, but it never supplies the grounded payload.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentProposal,
    AgentState,
    AgentTask,
    AgentTaskId,
    PermissionClass,
    ToolName,
    ToolResult,
)
from aaos.agent.orchestrator import AgentOrchestrator
from aaos.agent.policy import PermissionPolicy
from aaos.agent.proposal import ProposalRequest, ProposalResult
from aaos.agent.registry import (
    SideEffectClass,
    ToolDefinition,
    ToolField,
    ToolInputSchema,
    ToolRegistry,
    ToolValueType,
    ValidatedToolArguments,
)


def _task(task_id: str = "c2-task") -> AgentTask:
    return AgentTask(AgentTaskId(task_id), "Answer the question")


def _rag_output(
    *, status: str = "ANSWER", answer_text: str = "Trusted answer A"
) -> dict[str, object]:
    citations: list[dict[str, object]] = []
    if status != "INSUFFICIENT_EVIDENCE":
        citations.append(
            {
                "claim_id": "claim-1",
                "document_version_id": 7,
                "document_title": "source.pdf",
                "page_start": 3,
                "page_end": 3,
            }
        )
    return {
        "answer_text": answer_text,
        "citations": citations,
        "content_revision": 11,
        "generation_id": 4,
        "language": "en",
        "rendered_text": f"{answer_text}\n[source.pdf, page 3]" if citations else answer_text,
        "status": status,
    }


def _rag_proposal() -> ProposalResult:
    return ProposalResult.success(
        AgentProposal.from_mapping(
            {"tool_call": {"name": "rag_query", "arguments": {"question": "q"}}}
        )
    )


def _registry(
    handler: Callable[[ValidatedToolArguments], ToolResult], *, name: ToolName = ToolName.RAG_QUERY
) -> ToolRegistry:
    if name is ToolName.RAG_QUERY:
        # Mirror the application-owned RagQueryAdapter provenance marker. A
        # generic handler with the same logical name must not mint grounding.
        setattr(handler, "trusted_phase4_output", True)
    return ToolRegistry(
        (
            ToolDefinition(
                name=name,
                description="test tool",
                input_schema=ToolInputSchema(
                    (ToolField("question", ToolValueType.STRING),), "rag_query"
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=120,
                handler=handler,
            ),
        )
    )


def _orchestrator(adapter: object, registry: ToolRegistry) -> AgentOrchestrator:
    return AgentOrchestrator(
        adapter,  # type: ignore[arg-type]
        registry,
        PermissionPolicy.from_values({PermissionClass.READ_ONLY, PermissionClass.TERMINAL}),
    )


class _ReferenceAdapter:
    def __init__(self, proposal_factory: Callable[[str], ProposalResult]) -> None:
        self.requests: list[ProposalRequest] = []
        self._proposal_factory = proposal_factory

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            return _rag_proposal()
        fields = dict(request.observations[-1].fields)
        return self._proposal_factory(str(fields["grounded_result_ref"]))


def test_trusted_rag_reference_preserves_exact_grounded_payload() -> None:
    payload = _rag_output()

    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(payload)

    adapter = _ReferenceAdapter(
        lambda reference: ProposalResult.success(
            AgentProposal.from_mapping({"grounded_result_ref": reference})
        )
    )
    result = asyncio.run(_orchestrator(adapter, _registry(handler)).run(_task()))

    assert result.state is AgentState.COMPLETED
    assert result.result_kind.value == "GROUNDED_RAG_RESPONSE"
    assert result.grounded_rag_response is not None
    assert result.grounded_rag_response.payload == ToolResult.success(payload).output
    assert result.result_text == payload["rendered_text"]
    assert result.grounded_rag_response.task_id == _task().task_id


def test_model_terminal_text_is_explicit_control_not_grounded() -> None:
    class Adapter:
        async def propose(self, _: ProposalRequest) -> ProposalResult:
            return ProposalResult.success(AgentProposal(terminal_text="rewritten [999]"))

    result = asyncio.run(
        _orchestrator(Adapter(), _registry(lambda _: ToolResult.success({}))).run(_task())
    )

    assert result.state is AgentState.COMPLETED
    assert result.result_kind.value == "CONTROL_OR_CONVERSATIONAL_TERMINAL"
    assert result.grounded_rag_response is None
    assert result.grounded is False
    assert result.result_text == "rewritten [999]"


def test_terminal_proposals_require_trusted_terminal_capability() -> None:
    class Adapter:
        async def propose(self, _: ProposalRequest) -> ProposalResult:
            return ProposalResult.success(AgentProposal(terminal_text="done"))

    orchestrator = AgentOrchestrator(
        Adapter(),
        _registry(lambda _: ToolResult.success({})),
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )
    result = asyncio.run(orchestrator.run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.PERMISSION_DENIED


def test_grounded_reference_rejects_invented_and_cross_task_values() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(_rag_output())

    class Adapter:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            if not request.observations:
                return _rag_proposal()
            return ProposalResult.success(
                AgentProposal.from_mapping({"grounded_result_ref": "other-task-ref"})
            )

    result = asyncio.run(_orchestrator(Adapter(), _registry(handler)).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.INVALID_GROUNDED_REFERENCE


def test_phase4_refusal_cannot_be_upgraded_by_model_text_or_grounded_reference() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(
            _rag_output(status="INSUFFICIENT_EVIDENCE", answer_text="No evidence.")
        )

    class Adapter:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            if not request.observations:
                return _rag_proposal()
            return ProposalResult.success(
                AgentProposal(terminal_text="The course definitely says X. [1]")
            )

    result = asyncio.run(_orchestrator(Adapter(), _registry(handler)).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.INVALID_PROPOSAL
    assert result.grounded_rag_response is None


def test_phase4_answer_cannot_be_replaced_by_model_terminal_text() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(_rag_output())

    class Adapter:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            if not request.observations:
                return _rag_proposal()
            return ProposalResult.success(AgentProposal(terminal_text="forged replacement"))

    result = asyncio.run(_orchestrator(Adapter(), _registry(handler)).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.INVALID_PROPOSAL
    assert result.result_text is None
    assert result.grounded_rag_response is None


def test_refusal_grounded_reference_preserves_authoritative_refusal() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(
            _rag_output(status="INSUFFICIENT_EVIDENCE", answer_text="No evidence.")
        )

    adapter = _ReferenceAdapter(
        lambda reference: ProposalResult.success(
            AgentProposal.from_mapping({"grounded_result_ref": reference})
        )
    )
    result = asyncio.run(_orchestrator(adapter, _registry(handler)).run(_task()))

    assert result.state is AgentState.COMPLETED
    assert result.grounded_rag_response is not None
    assert result.grounded_rag_response.payload["status"] == "INSUFFICIENT_EVIDENCE"
    assert result.grounded_rag_response.payload["citations"] == ()
    assert result.result_kind.value == "GROUNDED_RAG_RESPONSE"


@pytest.mark.parametrize(
    "payload",
    [
        {"grounded_result_ref": "r", "terminal_text": "replace"},
        {"grounded_result_ref": "r", "citations": []},
        {"grounded_result_ref": "r", "status": "ANSWER"},
        {"grounded_result_ref": "r", "external_action_succeeded": True},
    ],
)
def test_model_cannot_supply_grounded_payload_or_external_authority(
    payload: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        AgentProposal.from_mapping(payload)


def test_non_rag_result_cannot_create_grounded_reference() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success({"summary": "state"})

    class Adapter:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            if not request.observations:
                return ProposalResult.success(
                    AgentProposal.from_mapping(
                        {
                            "tool_call": {
                                "name": "read_task_state",
                                "arguments": {"task_id": "c2-task"},
                            }
                        }
                    )
                )
            return ProposalResult.success(AgentProposal.from_mapping({"grounded_result_ref": "x"}))

    registry = ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.READ_TASK_STATE,
                description="state",
                input_schema=ToolInputSchema(
                    (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=5,
                handler=handler,
            ),
        )
    )
    result = asyncio.run(_orchestrator(Adapter(), registry).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.INVALID_GROUNDED_REFERENCE


def test_rag_named_generic_handler_cannot_mint_grounded_reference() -> None:
    def handler(_: ValidatedToolArguments) -> ToolResult:
        return ToolResult.success(_rag_output())

    class Adapter:
        async def propose(self, request: ProposalRequest) -> ProposalResult:
            if not request.observations:
                return _rag_proposal()
            return ProposalResult.success(AgentProposal.from_mapping({"grounded_result_ref": "x"}))

    # Same logical name, but no trusted Phase 4 provenance marker.
    registry = ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.RAG_QUERY,
                description="generic test tool",
                input_schema=ToolInputSchema(
                    (ToolField("question", ToolValueType.STRING),), "rag_query"
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=120,
                handler=handler,
            ),
        )
    )
    result = asyncio.run(_orchestrator(Adapter(), registry).run(_task()))

    assert result.state is AgentState.FAILED
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.INVALID_GROUNDED_REFERENCE


def test_malformed_rag_payload_cannot_become_a_trusted_lineage_object() -> None:
    from aaos.agent.contracts import GroundedRagResponse

    with pytest.raises(ValueError):
        GroundedRagResponse.from_tool_result(
            _task().task_id,
            "rag-ref",
            ToolResult.success({"status": "ANSWER", "answer_text": "forged"}),
        )


def test_c2_import_is_side_effect_free() -> None:
    __import__("aaos.agent.contracts")
    __import__("aaos.agent.orchestrator")
