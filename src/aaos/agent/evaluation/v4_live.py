"""Local-model executor with an application-owned fake-only tool registry."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from typing import Sequence

from aaos.agent.contracts import (
    AgentErrorCode,
    AgentProposal,
    AgentTask,
    AgentTaskId,
    PermissionClass,
    ToolName,
    ToolResult,
    TrustedTaskScope,
)
from aaos.agent.evaluation.contracts import ScopeOracle
from aaos.agent.evaluation.v2 import V2ExpectedAction
from aaos.agent.evaluation.v3 import (
    V3BenchmarkCase,
    V3EvaluationOwner,
    V3TrustedExecution,
    build_trusted_trace,
)
from aaos.agent.evaluation.v4 import V4EvaluationManifest, V4EvaluationTrace
from aaos.agent.evaluation.v4_runner import V4ExecutionBatch, V4ExecutionCounters
from aaos.agent.events import AgentEvent, AgentEventKind, InMemoryAgentEventSink
from aaos.agent.orchestrator import AgentOrchestrator
from aaos.agent.policy import PermissionPolicy
from aaos.agent.proposal import (
    AgentProposalAdapter,
    ProposalRequest,
    ProposalResult,
    RetrieveProposal,
    TerminalProposal,
)
from aaos.agent.registry import ToolRegistry, ValidatedToolArguments
from aaos.config import DEFAULT_SETTINGS
from aaos.inference.gateway import ModelGateway
from aaos.inference.lm_studio import LMStudioGateway
from aaos.inference.models import ChatRequest, ChatResponse, ModelAvailability
from aaos.rag.contracts import CourseScope, GlobalScope, HistoricalScope


class _FakeRagHandler:
    """Return one synthetic insufficient-evidence payload without touching Phase 4."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, arguments: ValidatedToolArguments) -> ToolResult:
        self.calls += 1
        values = arguments.to_mapping()
        language = values.get("answer_language", "en")
        if language not in {"en", "ar"}:
            language = "en"
        response_text = "Synthetic evaluation has no retrieved evidence."
        return ToolResult.success(
            {
                "answer_text": response_text,
                "citations": [],
                "content_revision": 0,
                "generation_id": 1,
                "language": language,
                "rendered_text": response_text,
                "status": "INSUFFICIENT_EVIDENCE",
            }
        )


def build_v4_fake_only_registry() -> ToolRegistry:
    """Build the exact capability allowlist used during V4 model evaluation."""

    defaults = {definition.name: definition for definition in ToolRegistry.default().definitions}
    rag = defaults.get(ToolName.RAG_QUERY)
    complete = defaults.get(ToolName.COMPLETE_TASK)
    if rag is None or complete is None:
        raise RuntimeError("fake-only tool registry is incomplete")
    fake_rag = replace(rag, handler=_FakeRagHandler())
    return ToolRegistry((fake_rag, complete))


@dataclass(slots=True)
class _RecordingProposalAdapter:
    adapter: AgentProposalAdapter
    results: list[ProposalResult]

    def configure_tool_registry(self, registry: ToolRegistry) -> None:
        self.adapter.configure_tool_registry(registry)

    async def propose(self, request: ProposalRequest) -> ProposalResult:
        result = await self.adapter.propose(request)
        self.results.append(result)
        return result


class _CountingGateway:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway
        self.calls = 0

    async def check_availability(self) -> ModelAvailability:
        return await self._gateway.check_availability()

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        return await self._gateway.generate(request)

    async def aclose(self) -> None:
        close = getattr(self._gateway, "aclose", None)
        if callable(close):
            await close()


class V4LocalModelExecutor:
    """Execute one canonical manifest with the configured local model and fake tools."""

    def __init__(self, gateway: ModelGateway) -> None:
        if not isinstance(gateway, ModelGateway):
            raise ValueError("gateway must implement the local model gateway contract")
        self._gateway = _CountingGateway(gateway)
        self._registry = build_v4_fake_only_registry()
        self._fake_rag_handler = next(
            definition.handler
            for definition in self._registry.definitions
            if definition.name is ToolName.RAG_QUERY
        )

    def __call__(self, manifest: V4EvaluationManifest) -> V4ExecutionBatch:
        return asyncio.run(self._execute_manifest_and_close(manifest))

    async def _execute_manifest_and_close(self, manifest: V4EvaluationManifest) -> V4ExecutionBatch:
        try:
            return await self._execute_manifest(manifest)
        finally:
            await self._gateway.aclose()

    async def _execute_manifest(self, manifest: V4EvaluationManifest) -> V4ExecutionBatch:
        traces: list[V4EvaluationTrace] = []
        invalid_output_count = 0
        schema_failure_count = 0
        parse_failure_count = 0
        unauthorized_write_count = 0
        arbitrary_tool_execution_count = 0
        credential_exposure_count = 0
        lifecycle_violation_count = 0
        policy_violation_count = 0
        for case in manifest.cases:
            trace, case_counters = await self._execute_case(case)
            traces.append(trace)
            invalid_output_count += case_counters["invalid_output_count"]
            schema_failure_count += case_counters["schema_failure_count"]
            parse_failure_count += case_counters["parse_failure_count"]
            unauthorized_write_count += case_counters["unauthorized_write_count"]
            arbitrary_tool_execution_count += case_counters["arbitrary_tool_execution_count"]
            credential_exposure_count += case_counters["credential_exposure_count"]
            lifecycle_violation_count += case_counters["lifecycle_violation_count"]
            policy_violation_count += case_counters["policy_violation_count"]

        aggregate_counters = V4ExecutionCounters(
            invalid_output_count=invalid_output_count,
            schema_failure_count=schema_failure_count,
            parse_failure_count=parse_failure_count,
            unauthorized_write_count=unauthorized_write_count,
            arbitrary_tool_execution_count=arbitrary_tool_execution_count,
            credential_exposure_count=credential_exposure_count,
            lifecycle_violation_count=lifecycle_violation_count,
            policy_violation_count=policy_violation_count,
            model_call_count=self._gateway.calls,
            rag_execution_count=0,
        )
        return V4ExecutionBatch(tuple(traces), aggregate_counters, fake_only=True)

    async def _execute_case(
        self, case: V3BenchmarkCase
    ) -> tuple[V4EvaluationTrace, dict[str, int]]:
        calls_before = self._gateway.calls
        adapter = _RecordingProposalAdapter(
            AgentProposalAdapter(self._gateway, tool_registry=self._registry), []
        )
        events = InMemoryAgentEventSink()
        orchestrator = AgentOrchestrator(
            adapter,
            registry=self._registry,
            permission_policy=PermissionPolicy.from_values(
                (PermissionClass.READ_ONLY, PermissionClass.TERMINAL)
            ),
            event_sink=events,
        )
        task = AgentTask(
            task_id=AgentTaskId(_task_id(case.case_id)),
            request_text=case.model_visible_input()["request_text"],
            initial_scope=_trusted_scope(case.trusted_scope_oracle),
        )
        result = await orchestrator.run(task)
        calls = self._gateway.calls - calls_before
        tool_calls = sum(event.event_kind is AgentEventKind.TOOL_STARTED for event in events.events)
        invalid_responses = adapter.adapter.invalid_output_count
        repairs = max(0, calls - len(adapter.results))
        proposal_diagnostics = sum(
            event.event_kind is AgentEventKind.PROPOSAL_REJECTED for event in events.events
        )
        observed_action = _observed_action(adapter.results)
        trace = V4EvaluationTrace(
            case_id=case.case_id,
            observed_action=observed_action,
            observed_terminal_kind=result.result_kind,
            observed_error_code=result.failure.code if result.failure is not None else None,
            proposal_format_valid=bool(adapter.results) and adapter.results[-1].ok,
            proposal_repairs=repairs,
            proposal_format_diagnostic_event_count=proposal_diagnostics,
            invalid_proposal_response_count=invalid_responses,
            model_calls=calls,
            model_unavailable=any(
                item.failure is not None and item.failure.code is AgentErrorCode.MODEL_UNAVAILABLE
                for item in adapter.results
            ),
            model_timeout=any(
                item.failure is not None and item.failure.code is AgentErrorCode.MODEL_TIMEOUT
                for item in adapter.results
            ),
            runtime_failure=result.failure is not None
            and result.failure.code
            not in {
                AgentErrorCode.INVALID_PROPOSAL,
                AgentErrorCode.MODEL_UNAVAILABLE,
                AgentErrorCode.MODEL_TIMEOUT,
            },
        )
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING:
            owner_evidence = _owner_evidence(case.owner, events.events, calls)
            if owner_evidence:
                trusted = build_trusted_trace(
                    case,
                    V3TrustedExecution(
                        case_id=case.case_id,
                        owner=case.owner,
                        condition_instantiated=True,
                        owner_exercised=True,
                        completed=result.failure is None,
                        observed_error_code=result.failure.code
                        if result.failure is not None
                        else None,
                        observed_terminal_kind=result.result_kind,
                        permission_allowed=_permission_observed(events.events, result.failure),
                        grounded=result.grounded,
                        tool_calls=tool_calls,
                    ),
                )
                trace = replace(
                    trace,
                    trusted_condition_instantiated=trusted.trusted_condition_instantiated,
                    trusted_owner_exercised=trusted.trusted_owner_exercised,
                    trusted_condition_passed=trusted.trusted_condition_passed,
                    security_hard_gate_failure_count=trusted.security_failure_count,
                    security_hard_gate_failed=trusted.security_hard_gate_failed,
                )
            else:
                trace = replace(
                    trace,
                    trusted_condition_instantiated=True,
                    trusted_owner_exercised=False,
                    trusted_condition_passed=False,
                )
        counters = {
            "invalid_output_count": invalid_responses,
            "schema_failure_count": adapter.adapter.schema_failure_count,
            "parse_failure_count": adapter.adapter.parse_failure_count,
            "unauthorized_write_count": 0,
            "arbitrary_tool_execution_count": 0,
            "credential_exposure_count": 0,
            "lifecycle_violation_count": sum(
                event.event_kind is AgentEventKind.TOOL_OUTCOME_UNKNOWN for event in events.events
            ),
            "policy_violation_count": 0,
        }
        return trace, counters

    async def close(self) -> None:
        close = getattr(self._gateway._gateway, "aclose", None)
        if callable(close):
            await close()


def check_configured_local_model() -> None:
    import httpx2

    gateway = LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())

    async def check_and_close() -> bool:
        try:
            availability = await gateway.check_availability()
            return availability.reachable and availability.available
        finally:
            await gateway.aclose()

    try:
        available = asyncio.run(check_and_close())
    except Exception:
        raise RuntimeError("configured local model is unavailable") from None
    if not available:
        raise RuntimeError("configured local model is unavailable")


def _task_id(case_id: str) -> str:
    return "v4-" + hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:24]


def create_configured_local_executor() -> V4LocalModelExecutor:
    import httpx2

    return V4LocalModelExecutor(LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport()))


def _trusted_scope(oracle: ScopeOracle) -> TrustedTaskScope | None:
    if oracle is ScopeOracle.NONE:
        return None
    if oracle is ScopeOracle.COURSE:
        return TrustedTaskScope(CourseScope(course_id=1))
    if oracle is ScopeOracle.GLOBAL:
        return TrustedTaskScope(GlobalScope())
    if oracle is ScopeOracle.HISTORICAL:
        return TrustedTaskScope(HistoricalScope(document_version_ids=(1,)))
    raise ValueError("trusted scope configuration is unsupported")


def _observed_action(results: Sequence[ProposalResult]) -> V2ExpectedAction | None:
    if not results or results[0].proposal is None:
        return None
    proposal = results[0].proposal
    if isinstance(proposal, RetrieveProposal):
        return V2ExpectedAction.RETRIEVE
    if isinstance(proposal, TerminalProposal):
        return V2ExpectedAction.TERMINAL_TEXT
    if isinstance(proposal, AgentProposal):
        if proposal.tool_call is not None and proposal.tool_call.name is ToolName.RAG_QUERY:
            return V2ExpectedAction.RETRIEVE
        if proposal.terminal_text is not None:
            return V2ExpectedAction.TERMINAL_TEXT
    return None


def _owner_evidence(
    owner: V3EvaluationOwner,
    events: tuple[AgentEvent, ...],
    model_calls: int,
) -> bool:
    kinds = {event.event_kind for event in events}
    if owner is V3EvaluationOwner.TRUSTED_PRE_MODEL_CONTROL:
        return AgentEventKind.RUNTIME_POLICY_VALIDATED in kinds
    if owner is V3EvaluationOwner.TRUSTED_POST_MODEL_VALIDATION:
        return bool(kinds & {AgentEventKind.PROPOSAL_ACCEPTED, AgentEventKind.PROPOSAL_REJECTED})
    if owner is V3EvaluationOwner.TRUSTED_RUNTIME_OR_LIFECYCLE:
        return bool(
            kinds
            & {
                AgentEventKind.TOOL_STARTED,
                AgentEventKind.TOOL_COMPLETED,
                AgentEventKind.TOOL_FAILED,
                AgentEventKind.TOOL_DEADLINE_EXCEEDED,
                AgentEventKind.TOOL_OUTCOME_UNKNOWN,
                AgentEventKind.TERMINAL_RESULT_EMITTED,
            }
        )
    if owner is V3EvaluationOwner.TRUSTED_POST_TOOL_RESULT:
        return bool(kinds & {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED})
    if owner is V3EvaluationOwner.EVALUATION_SYNTHETIC_FAULT_INJECTION:
        return model_calls > 0 and AgentEventKind.TASK_STARTED in kinds
    return False


def _permission_observed(events: tuple[AgentEvent, ...], failure: object | None) -> bool | None:
    if any(event.event_kind is AgentEventKind.TOOL_STARTED for event in events):
        return True
    if getattr(failure, "code", None) is AgentErrorCode.PERMISSION_DENIED:
        return False
    return None
