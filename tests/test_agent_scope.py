"""C1 trusted task-scope authorization tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from aaos.agent import (
    AgentErrorCode,
    AgentOrchestrator,
    AgentProposal,
    AgentTask,
    AgentTaskId,
    AgentTerminalResult,
    PermissionClass,
    PermissionPolicy,
    ProposalResult,
    RagQueryAdapter,
    ToolArguments,
    ToolName,
    TrustedTaskScope,
    authorize_scope,
    build_read_tool_registry,
)
from aaos.rag import CourseScope, GlobalScope, HistoricalScope, RagQuery, RenderedRagResponse


@dataclass
class _FakeRagService:
    calls: list[RagQuery]

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        self.calls.append(query)
        raise AssertionError("the scope test service must not construct a response")


class _ScriptedProposalAdapter:
    def __init__(self, proposal: AgentProposal) -> None:
        self.proposal = proposal

    async def propose(self, _request: object) -> ProposalResult:
        return ProposalResult.success(self.proposal)


def _proposal(scope: dict[str, object], question: str = "What is due?") -> AgentProposal:
    return AgentProposal.from_mapping(
        {
            "tool_call": {
                "name": "rag_query",
                "arguments": {"question": question, "scope": scope},
            }
        }
    )


def _task(scope: TrustedTaskScope | None) -> AgentTask:
    return AgentTask(AgentTaskId("scope-task"), "Answer the question", initial_scope=scope)


def _run(
    scope: TrustedTaskScope | None, proposed: dict[str, object]
) -> tuple[AgentTerminalResult, _FakeRagService]:
    service = _FakeRagService([])
    result = asyncio.run(
        AgentOrchestrator(
            _ScriptedProposalAdapter(_proposal(proposed)),
            build_read_tool_registry(service),
            PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
        ).run(_task(scope))
    )
    return result, service


def test_trusted_task_scope_is_closed_and_serializes_phase4_identity() -> None:
    grant = TrustedTaskScope(CourseScope(342))

    assert grant.scope == CourseScope(342)
    assert grant.to_dict() == {"kind": "course", "course_id": 342}
    with pytest.raises(TypeError):
        TrustedTaskScope(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("trusted", "proposed", "allowed"),
    [
        (CourseScope(342), CourseScope(342), True),
        (CourseScope(342), CourseScope(999), False),
        (CourseScope(342), GlobalScope(), False),
        (CourseScope(342), HistoricalScope((7,)), False),
        (GlobalScope(), GlobalScope(), True),
        (GlobalScope(), CourseScope(342), False),
        (GlobalScope(), HistoricalScope((7,)), False),
        (HistoricalScope((7, 8)), HistoricalScope((8,)), True),
        (HistoricalScope((7, 8)), HistoricalScope((7, 9)), False),
    ],
)
def test_scope_authorization_is_deterministic_and_fail_closed(
    trusted: object, proposed: object, allowed: bool
) -> None:
    decision = authorize_scope(proposed, TrustedTaskScope(trusted))  # type: ignore[arg-type]

    assert decision.allowed is allowed
    assert decision.reason_code is (
        AgentErrorCode.SCOPE_GRANTED if allowed else AgentErrorCode.SCOPE_DENIED
    )


def test_historical_allowlist_rejects_duplicate_proposed_versions() -> None:
    decision = authorize_scope(HistoricalScope((7, 7)), TrustedTaskScope(HistoricalScope((7, 8))))

    assert decision.allowed is False
    assert decision.reason_code is AgentErrorCode.SCOPE_DENIED


@pytest.mark.parametrize(
    "proposed",
    [
        {"kind": "course", "course_id": 999},
        {"kind": "global"},
        {"kind": "historical", "document_version_ids": [7]},
    ],
)
def test_course_grant_rejects_scope_escape_before_phase4(
    proposed: dict[str, object],
) -> None:
    result, service = _run(TrustedTaskScope(CourseScope(342)), proposed)

    assert result.state.value == "FAILED"
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_same_course_scope_reaches_phase4_once() -> None:
    result, service = _run(TrustedTaskScope(CourseScope(342)), {"kind": "course", "course_id": 342})

    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.RAG_QUERY_FAILED
    assert len(service.calls) == 1
    assert service.calls[0].scope == CourseScope(342)


def test_direct_registry_rag_call_without_trusted_scope_is_denied_before_phase4() -> None:
    service = _FakeRagService([])
    registry = build_read_tool_registry(service)
    result = registry.execute(
        _proposal({"kind": "global"}),
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []

    payload_result = registry.execute_payload(
        {
            "tool_call": {
                "name": "rag_query",
                "arguments": {"question": "q", "scope": {"kind": "global"}},
            }
        },
        PermissionPolicy.from_values({PermissionClass.READ_ONLY}),
    )
    assert payload_result.ok is False
    assert payload_result.error is not None
    assert payload_result.error.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_direct_phase4_adapter_call_without_trusted_scope_is_denied() -> None:
    service = _FakeRagService([])
    schema = build_read_tool_registry(service).get(ToolName.RAG_QUERY).input_schema
    arguments = schema.validate(
        ToolArguments.from_mapping({"question": "q", "scope": {"kind": "global"}})
    )

    result = RagQueryAdapter(service).handle(arguments)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_explicit_global_grant_allows_only_explicit_global_query() -> None:
    result, service = _run(TrustedTaskScope(GlobalScope()), {"kind": "global"})

    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.RAG_QUERY_FAILED
    assert len(service.calls) == 1
    assert isinstance(service.calls[0].scope, GlobalScope)

    narrowed_result, narrowed_service = _run(
        TrustedTaskScope(GlobalScope()), {"kind": "course", "course_id": 342}
    )
    assert narrowed_result.failure is not None
    assert narrowed_result.failure.code is AgentErrorCode.SCOPE_DENIED
    assert narrowed_service.calls == []


def test_historical_grant_cannot_be_widened() -> None:
    result, service = _run(
        TrustedTaskScope(HistoricalScope((7, 8))),
        {"kind": "historical", "document_version_ids": [7, 9]},
    )

    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_missing_trusted_scope_denies_valid_model_scope() -> None:
    result, service = _run(None, {"kind": "global"})

    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_prompt_or_observation_text_cannot_broaden_trusted_scope() -> None:
    result, service = _run(
        TrustedTaskScope(CourseScope(342)),
        {"kind": "global"},
    )

    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.SCOPE_DENIED
    assert service.calls == []


def test_scope_authorization_is_separate_from_capability_permission() -> None:
    result, service = _run(TrustedTaskScope(CourseScope(342)), {"kind": "course", "course_id": 342})
    assert result.failure is not None
    assert result.failure.code is AgentErrorCode.RAG_QUERY_FAILED
    assert len(service.calls) == 1

    denied_service = _FakeRagService([])
    denied = asyncio.run(
        AgentOrchestrator(
            _ScriptedProposalAdapter(_proposal({"kind": "course", "course_id": 342})),
            build_read_tool_registry(denied_service),
            PermissionPolicy(),
        ).run(_task(TrustedTaskScope(CourseScope(342))))
    )
    assert denied.failure is not None
    assert denied.failure.code is AgentErrorCode.PERMISSION_DENIED
    assert denied_service.calls == []
