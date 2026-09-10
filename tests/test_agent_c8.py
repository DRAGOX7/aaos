"""C8 least-authority proposal contract tests (RED before implementation)."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from aaos.agent.contracts import (
    AgentTask,
    AgentTaskId,
    PermissionClass,
    ToolName,
    ToolResult,
    TrustedTaskScope,
)
from aaos.agent.orchestrator import AgentOrchestrator
from aaos.agent.policy import PermissionPolicy
from aaos.agent.proposal import (
    PROPOSAL_SYSTEM_PROMPT,
    AgentProposalAdapter,
    ProposalRequest,
    ProposalResult,
    ProviderProposalEnvelope,
    RetrieveProposal,
    TerminalProposal,
)
from aaos.agent.registry import (
    SideEffectClass,
    ToolDefinition,
    ToolField,
    ToolInputSchema,
    ToolRegistry,
    ToolValueType,
    make_observation_projector,
    validate_generic_tool_result,
)
from aaos.inference import ChatRequest, ChatResponse, ModelAvailability, TokenUsage
from aaos.inference.lm_studio import _STRUCTURED_RESPONSE_FORMATS
from aaos.rag import GlobalScope


def test_c8_instruction_uses_general_information_source_boundary() -> None:
    prompt = " ".join(PROPOSAL_SYSTEM_PROMPT.split())
    assert "requires trusted local, user, course, or project material" in prompt
    assert "If it does, choose retrieve" in prompt
    assert "If it does not, choose terminal_text" in prompt
    assert "Do not retrieve merely because a request is technical" in prompt
    assert "Do not choose terminal_text merely because a plausible general answer exists" in prompt
    for forbidden in ("cal-", "v4-", "ambiguous_task", "oracle", "English", "Arabic"):
        assert forbidden not in prompt


class _Gateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ChatRequest] = []

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(
            reachable=True,
            available=True,
            configured_model="qwen/qwen3.5-4b",
            available_models=("qwen/qwen3.5-4b",),
            loaded_context_lengths=(8192,),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            content=self.content,
            model_identifier="qwen/qwen3.5-4b",
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def test_retrieve_transport_has_no_model_authority_fields() -> None:
    envelope = ProviderProposalEnvelope.from_mapping({"action": "retrieve"})
    proposal = envelope.to_canonical()
    assert isinstance(proposal, RetrieveProposal)
    assert proposal.to_dict() == {"action": "retrieve"}


def test_terminal_transport_contains_only_bounded_text() -> None:
    envelope = ProviderProposalEnvelope.from_mapping(
        {"action": "terminal_text", "terminal_text": "Need clarification."}
    )
    proposal = envelope.to_canonical()
    assert isinstance(proposal, TerminalProposal)
    assert proposal.to_dict() == {"action": "terminal_text", "terminal_text": "Need clarification."}


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "tool_call", "tool_name": "rag_query", "arguments": {}},
        {"action": "retrieve", "tool_name": "rag_query"},
        {"action": "retrieve", "scope": {"kind": "global"}},
        {"action": "retrieve", "permission": "READ_ONLY"},
        {"action": "retrieve", "runtime_policy": {}},
        {"action": "retrieve", "grounded": True},
        {"action": "retrieve", "question": "rewritten"},
        {"action": "retrieve", "answer_language": "ar"},
        {"action": "retrieve", "terminal_text": "not empty"},
        {"action": "terminal_text", "terminal_text": ""},
        {"action": "unknown"},
    ],
)
def test_c8_provider_authority_and_invalid_actions_fail_closed(payload: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ProviderProposalEnvelope.from_mapping(payload)


def test_adapter_uses_c8_schema_and_returns_retrieve_variant() -> None:
    gateway = _Gateway('{"action":"retrieve"}')
    result = asyncio.run(
        AgentProposalAdapter(gateway).propose(ProposalRequest("Find the next deadline"))
    )
    assert result.ok
    assert isinstance(result.proposal, RetrieveProposal)
    request = gateway.requests[0]
    assert request.structured_output_schema == "least_authority_proposal_transport"
    assert request.max_output_tokens == 512
    assert "AVAILABLE_TOOLS_JSON" not in request.messages[0].content


def test_c8_declares_one_useful_rag_call_ceiling() -> None:
    from aaos.agent import USEFUL_RAG_CALLS_PER_REQUEST_MAX

    assert USEFUL_RAG_CALLS_PER_REQUEST_MAX == 1


def test_adapter_terminal_is_bounded_and_non_grounded() -> None:
    gateway = _Gateway('{"action":"terminal_text","terminal_text":"done"}')
    result = asyncio.run(AgentProposalAdapter(gateway).propose(ProposalRequest("request")))
    assert result.ok
    assert isinstance(result.proposal, TerminalProposal)
    assert len(result.proposal.terminal_text) <= 16_384


def test_c8_schema_is_flat_and_contains_no_authority_fields() -> None:
    transport = cast(
        dict[str, object], _STRUCTURED_RESPONSE_FORMATS["least_authority_proposal_transport"]
    )
    wrapper = cast(dict[str, object], transport["json_schema"])
    schema = cast(dict[str, object], wrapper["schema"])
    properties = cast(dict[str, object], schema["properties"])
    action = cast(dict[str, object], properties["action"])
    assert transport["type"] == "json_schema"
    assert wrapper["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(properties) == {"action", "terminal_text"}
    assert action["enum"] == ["retrieve", "terminal_text"]


def test_retrieve_uses_trusted_scope_and_directly_returns_rag_lineage() -> None:
    calls = {"proposals": 0, "handlers": 0}

    def handler(_arguments: object) -> ToolResult:
        calls["handlers"] += 1
        return ToolResult.success(
            {
                "answer_text": "Trusted answer",
                "citations": (
                    {
                        "claim_id": "c1",
                        "document_version_id": 1,
                        "document_title": "source.pdf",
                        "page_start": 1,
                        "page_end": 1,
                    },
                ),
                "content_revision": 1,
                "generation_id": 1,
                "language": "en",
                "rendered_text": "Trusted answer [source.pdf, page 1]",
                "status": "ANSWER",
            }
        )

    setattr(handler, "trusted_phase4_output", True)
    registry = ToolRegistry(
        (
            ToolDefinition(
                name=ToolName.RAG_QUERY,
                description="trusted rag",
                input_schema=ToolInputSchema(
                    (
                        ToolField("question", ToolValueType.STRING),
                        ToolField("scope", ToolValueType.OBJECT),
                    ),
                    "rag_query",
                ),
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=120,
                handler=handler,
                output_validator=validate_generic_tool_result,
                observation_projector=make_observation_projector(ToolName.RAG_QUERY),
            ),
        )
    )

    class Adapter:
        max_structured_proposal_repairs = 1
        model_proposal_timeout_seconds = 60.0

        async def propose(self, _request: ProposalRequest) -> ProposalResult:
            calls["proposals"] += 1
            return ProposalResult.success(RetrieveProposal())

    task = AgentTask(
        AgentTaskId("direct-rag"),
        "Find the next deadline",
        initial_scope=TrustedTaskScope(GlobalScope()),
    )
    result = asyncio.run(
        AgentOrchestrator(
            Adapter(),
            registry,
            PermissionPolicy.from_values({PermissionClass.READ_ONLY, PermissionClass.TERMINAL}),
        ).run(task)
    )
    assert result.grounded, result.to_dict()
    assert calls == {"proposals": 1, "handlers": 1}
