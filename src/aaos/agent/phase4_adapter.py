"""Bounded read-only adapters for the frozen Phase 4 RAG boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol, TypeAlias

from aaos.rag import (
    CourseScope,
    GlobalScope,
    HistoricalScope,
    LocalRagService,
    RagAnswer,
    RagAnswerStatus,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
    RagScope,
    RenderedRagResponse,
)

from .contracts import (
    AgentErrorCode,
    PermissionClass,
    ToolError,
    ToolName,
    ToolObservation,
    ToolResult,
    TrustedTaskScope,
)
from .policy import authorize_scope, parse_proposed_scope
from .registry import (
    SideEffectClass,
    ToolDefinition,
    ToolRegistry,
    ValidatedToolArguments,
)

MAX_RAG_QUERY_CHARACTERS = 4_096
MAX_RAG_CITATIONS = 64

RagOutput: TypeAlias = dict[str, object]


class _RagService(Protocol):
    async def ask(self, query: RagQuery) -> RenderedRagResponse: ...


def _positive_id(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _bounded_question(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("question must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("question must not be blank")
    if len(normalized) > MAX_RAG_QUERY_CHARACTERS:
        raise ValueError("question exceeds the adapter limit")
    return normalized


def _parse_scope(value: object) -> CourseScope | GlobalScope | HistoricalScope:
    return parse_proposed_scope(value)


def parse_rag_scope(value: object) -> RagScope:
    """Parse a request-local scope without invoking the Phase 4 service."""

    return parse_proposed_scope(value)


def _parse_query(arguments: ValidatedToolArguments) -> RagQuery:
    if not isinstance(arguments, ValidatedToolArguments) or arguments.schema_name != "rag_query":
        raise ValueError("arguments are not validated for rag_query")
    values = arguments.to_mapping()
    allowed = {"question", "scope", "answer_language"}
    if set(values) - allowed:
        raise ValueError("rag query contains unknown fields")
    question = _bounded_question(values.get("question"))
    scope = _parse_scope(values.get("scope"))
    answer_language = values.get("answer_language")
    if answer_language is not None:
        if not isinstance(answer_language, str) or answer_language.strip().casefold() not in {
            "en",
            "ar",
        }:
            raise ValueError("answer_language must be en or ar")
        answer_language = answer_language.strip().casefold()
    return RagQuery(question, scope, answer_language)


def _citation_payload(citation: object) -> dict[str, object]:
    if isinstance(citation, Mapping):
        if set(citation) != {
            "claim_id",
            "document_version_id",
            "document_title",
            "page_start",
            "page_end",
        }:
            raise ValueError("citation is malformed")
    else:
        if not hasattr(citation, "claim_id"):
            raise ValueError("citation is malformed")
    values: dict[str, object] = {}
    for field in ("claim_id", "document_version_id", "document_title", "page_start", "page_end"):
        value = (
            citation.get(field) if isinstance(citation, Mapping) else getattr(citation, field, None)
        )
        if field in {"claim_id", "document_title"}:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("citation text is malformed")
            values[field] = value.strip()
        elif field in {"document_version_id", "page_start", "page_end"}:
            values[field] = _positive_id(value, field)
        else:
            raise ValueError("citation is malformed")
    if values["page_end"] < values["page_start"]:  # type: ignore[operator]
        raise ValueError("citation page range is malformed")
    return values


def _response_payload(response: object) -> RagOutput:
    if not isinstance(response, RenderedRagResponse):
        raise ValueError("Phase 4 returned an invalid response")
    answer: RagAnswer = response.answer
    if not isinstance(answer, RagAnswer) or not isinstance(answer.status, RagAnswerStatus):
        raise ValueError("Phase 4 returned an invalid answer")
    if not isinstance(answer.language, str) or answer.language.casefold() not in {"en", "ar"}:
        raise ValueError("Phase 4 returned an invalid language")
    if not isinstance(answer.answer_text, str) or not answer.answer_text.strip():
        raise ValueError("Phase 4 returned empty answer text")
    if not isinstance(response.rendered_text, str) or not response.rendered_text.strip():
        raise ValueError("Phase 4 returned empty rendered text")
    citations = answer.citations
    if not isinstance(citations, tuple) or len(citations) > MAX_RAG_CITATIONS:
        raise ValueError("Phase 4 returned too many citations")
    citation_payloads = tuple(_citation_payload(citation) for citation in citations)
    if answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE and citation_payloads:
        raise ValueError("refusal cannot contain citations")
    return {
        "answer_text": answer.answer_text,
        "citations": citation_payloads,
        "content_revision": answer.content_revision,
        "generation_id": answer.generation_id,
        "language": answer.language.casefold(),
        "rendered_text": response.rendered_text,
        "status": answer.status.value,
    }


def _safe_rag_failure(error: RagOperationalError | None = None) -> ToolResult:
    mapping = {
        RagErrorCode.MODEL_UNAVAILABLE: AgentErrorCode.MODEL_UNAVAILABLE,
        RagErrorCode.MODEL_TIMEOUT: AgentErrorCode.MODEL_TIMEOUT,
    }
    code = (
        mapping.get(error.code, AgentErrorCode.RAG_QUERY_FAILED)
        if error is not None
        else AgentErrorCode.RAG_QUERY_FAILED
    )
    message = {
        AgentErrorCode.MODEL_UNAVAILABLE: "RAG model is unavailable",
        AgentErrorCode.MODEL_TIMEOUT: "RAG model timed out",
    }.get(code, "RAG query failed")
    return ToolResult.failure(
        ToolError(code, message, retryable=code is not AgentErrorCode.RAG_QUERY_FAILED)
    )


class RagQueryAdapter:
    """Translate validated tool arguments into one frozen Phase 4 query."""

    # Trusted provenance marker consumed by the Phase 5 C2 boundary. Merely
    # naming a custom tool ``rag_query`` is insufficient to create grounded
    # output; only this application-owned Phase 4 adapter may mint a lineage
    # reference.
    trusted_phase4_output = True

    def __init__(self, service: LocalRagService | _RagService) -> None:
        if not callable(getattr(service, "ask", None)):
            raise TypeError("service must expose LocalRagService.ask")
        self._service = service

    async def handle_async(
        self,
        arguments: ValidatedToolArguments,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ToolResult:
        try:
            query = _parse_query(arguments)
        except (TypeError, ValueError):
            return ToolResult.failure(
                ToolError(AgentErrorCode.INVALID_TOOL_ARGUMENTS, "RAG query arguments are invalid")
            )
        if not authorize_scope(query.scope, trusted_task_scope).allowed:
            return ToolResult.failure(
                ToolError(AgentErrorCode.SCOPE_DENIED, "RAG scope was not authorized")
            )
        try:
            response = await self._service.ask(query)
            return ToolResult.success(_response_payload(response))
        except RagOperationalError as error:
            return _safe_rag_failure(error)
        except (TypeError, ValueError, AttributeError):
            return _safe_rag_failure()
        except Exception:
            return _safe_rag_failure()

    def handle(
        self,
        arguments: ValidatedToolArguments,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ToolResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.handle_async(arguments, trusted_task_scope=trusted_task_scope))
        return ToolResult.failure(
            ToolError(AgentErrorCode.RAG_QUERY_FAILED, "RAG query requires an async boundary")
        )

    def handle_with_scope(
        self, arguments: ValidatedToolArguments, trusted_task_scope: TrustedTaskScope | None
    ) -> ToolResult:
        return self.handle(arguments, trusted_task_scope=trusted_task_scope)

    def __call__(self, arguments: ValidatedToolArguments) -> ToolResult:
        return self.handle(arguments)


def _validate_observation_output(output: object) -> Mapping[str, object]:
    if not isinstance(output, Mapping):
        raise ValueError("result output must be a mapping")
    required = {
        "answer_text",
        "citations",
        "content_revision",
        "generation_id",
        "language",
        "rendered_text",
        "status",
    }
    if set(output) != required:
        raise ValueError("result output has an invalid schema")
    payload = _response_payload_from_mapping(output)
    return payload


def _response_payload_from_mapping(output: Mapping[str, object]) -> RagOutput:
    status = output.get("status")
    if not isinstance(status, str) or status not in {item.value for item in RagAnswerStatus}:
        raise ValueError("result output status is invalid")
    answer_text = output.get("answer_text")
    rendered_text = output.get("rendered_text")
    language = output.get("language")
    if not isinstance(answer_text, str) or not answer_text.strip():
        raise ValueError("result output answer_text is invalid")
    if not isinstance(rendered_text, str) or not rendered_text.strip():
        raise ValueError("result output rendered_text is invalid")
    if not isinstance(language, str) or language not in {"en", "ar"}:
        raise ValueError("result output language is invalid")
    generation_id = _positive_id(output.get("generation_id"), "generation_id")
    revision = output.get("content_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("result output content_revision is invalid")
    citations = output.get("citations")
    if not isinstance(citations, (tuple, list)) or len(citations) > MAX_RAG_CITATIONS:
        raise ValueError("result output citations are invalid")
    normalized_citations = tuple(_citation_payload(item) for item in citations)
    if status == RagAnswerStatus.INSUFFICIENT_EVIDENCE.value and normalized_citations:
        raise ValueError("refusal cannot contain citations")
    return {
        "answer_text": answer_text,
        "citations": normalized_citations,
        "content_revision": revision,
        "generation_id": generation_id,
        "language": language,
        "rendered_text": rendered_text,
        "status": status,
    }


def rag_result_to_observation(result: ToolResult) -> ToolObservation:
    """Project one validated RAG result into a bounded model-visible observation."""

    if not isinstance(result, ToolResult):
        raise ValueError("result must be a ToolResult")
    if not result.ok:
        if result.error is None:
            raise ValueError("failed result requires an error")
        return ToolObservation(
            kind="rag_query",
            status="ERROR",
            text=result.error.message,
            fields=(
                ("error_code", result.error.code.value),
                ("retryable", result.error.retryable),
            ),
        )
    payload = _validate_observation_output(result.output)
    return ToolObservation(
        kind="rag_query",
        status="OK",
        text=str(payload["answer_text"]),
        fields=(
            ("citations", payload["citations"]),
            ("content_revision", payload["content_revision"]),
            ("generation_id", payload["generation_id"]),
            ("language", payload["language"]),
            ("status", payload["status"]),
        ),
    )


def build_read_tool_registry(service: LocalRagService | _RagService) -> ToolRegistry:
    """Build the static registry with only the Phase 4 read adapter enabled."""

    adapter = RagQueryAdapter(service)
    definitions: list[ToolDefinition] = []
    for definition in ToolRegistry.default().definitions:
        if definition.name is not ToolName.RAG_QUERY:
            definitions.append(definition)
            continue
        definitions.append(
            ToolDefinition(
                name=definition.name,
                description=definition.description,
                input_schema=definition.input_schema,
                required_permission=PermissionClass.READ_ONLY,
                side_effect=SideEffectClass.READ_ONLY,
                timeout_seconds=definition.timeout_seconds,
                handler=adapter,
                observation_kind=definition.observation_kind,
                result_schema="rag_query_result",
                sanitization_policy="rag_safe_summary",
                audit_version=definition.audit_version,
            )
        )
    return ToolRegistry(tuple(definitions))


__all__ = [
    "MAX_RAG_CITATIONS",
    "MAX_RAG_QUERY_CHARACTERS",
    "RagQueryAdapter",
    "build_read_tool_registry",
    "parse_rag_scope",
    "rag_result_to_observation",
]
