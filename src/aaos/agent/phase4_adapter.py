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
from .policy import RAG_ANSWER_LANGUAGES, authorize_scope, parse_proposed_scope
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
    allowed = {"question", "scope", "answer_language", "context_hint"}
    if set(values) - allowed:
        raise ValueError("rag query contains unknown fields")
    question = _bounded_question(values.get("question"))
    scope = _parse_scope(values.get("scope"))
    answer_language = values.get("answer_language")
    if answer_language is not None:
        if not isinstance(answer_language, str) or answer_language.strip().casefold() not in set(
            RAG_ANSWER_LANGUAGES
        ):
            raise ValueError("answer_language must be en or ar")
        answer_language = answer_language.strip().casefold()
    context_hint = values.get("context_hint")
    if context_hint is not None and not isinstance(context_hint, str):
        raise ValueError("context_hint must be text or None")
    return RagQuery(question, scope, answer_language, context_hint)


def _citation_payload(citation: object) -> dict[str, object]:
    page_fields = {
        "claim_id",
        "document_version_id",
        "document_title",
        "page_start",
        "page_end",
    }
    structure_fields = {
        "claim_id",
        "document_version_id",
        "document_title",
        "unit_start",
        "unit_end",
        "heading",
        "table_index",
        "row_index",
        "column_index",
    }

    def read(field: str) -> object:
        if isinstance(citation, Mapping):
            return citation.get(field)
        return getattr(citation, field, None)

    if isinstance(citation, Mapping):
        keys = set(citation)
        if keys == page_fields:
            mode = "PAGE"
        elif keys == structure_fields:
            mode = "STRUCTURE"
        else:
            raise ValueError("citation is malformed")
    else:
        has_page = all(hasattr(citation, field) for field in ("page_start", "page_end"))
        has_structure = all(hasattr(citation, field) for field in ("unit_start", "unit_end"))

        if has_page == has_structure:
            raise ValueError("citation is malformed")

        mode = "PAGE" if has_page else "STRUCTURE"

    claim_id = read("claim_id")
    document_title = read("document_title")

    if not isinstance(claim_id, str) or not claim_id.strip():
        raise ValueError("claim_id must contain text")
    if not isinstance(document_title, str) or not document_title.strip():
        raise ValueError("document_title must contain text")

    document_version_id = _positive_id(
        read("document_version_id"),
        "document_version_id",
    )

    if mode == "PAGE":
        page_start = _positive_id(
            read("page_start"),
            "page_start",
        )
        page_end = _positive_id(
            read("page_end"),
            "page_end",
        )
        if page_end < page_start:
            raise ValueError("citation page range is invalid")

        return {
            "claim_id": claim_id,
            "document_version_id": document_version_id,
            "document_title": document_title,
            "page_start": page_start,
            "page_end": page_end,
        }

    unit_start = _positive_id(
        read("unit_start"),
        "unit_start",
    )
    unit_end = _positive_id(
        read("unit_end"),
        "unit_end",
    )
    if unit_end < unit_start:
        raise ValueError("citation unit range is invalid")

    heading = read("heading")
    if heading is not None and (not isinstance(heading, str) or not heading.strip()):
        raise ValueError("heading must contain text")

    coordinates: list[int | None] = []
    for field in (
        "table_index",
        "row_index",
        "column_index",
    ):
        value = read(field)
        if value is None:
            coordinates.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative integer or None")
        coordinates.append(value)

    if any(value is None for value in coordinates) and not all(
        value is None for value in coordinates
    ):
        raise ValueError("structural table coordinates must be all present or all absent")

    return {
        "claim_id": claim_id,
        "document_version_id": document_version_id,
        "document_title": document_title,
        "unit_start": unit_start,
        "unit_end": unit_end,
        "heading": heading,
        "table_index": coordinates[0],
        "row_index": coordinates[1],
        "column_index": coordinates[2],
    }


def _response_payload(response: object) -> RagOutput:
    if not isinstance(response, RenderedRagResponse):
        raise ValueError("Phase 4 returned an invalid response")
    answer: RagAnswer = response.answer
    if not isinstance(answer, RagAnswer) or not isinstance(answer.status, RagAnswerStatus):
        raise ValueError("Phase 4 returned an invalid answer")
    if not isinstance(answer.language, str) or answer.language.casefold() not in set(
        RAG_ANSWER_LANGUAGES
    ):
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
        RagErrorCode.NO_ELIGIBLE_DOCUMENTS: AgentErrorCode.NO_INDEXED_DOCUMENTS,
    }
    code = (
        mapping.get(error.code, AgentErrorCode.RAG_QUERY_FAILED)
        if error is not None
        else AgentErrorCode.RAG_QUERY_FAILED
    )
    message = {
        AgentErrorCode.MODEL_UNAVAILABLE: "RAG model is unavailable",
        AgentErrorCode.MODEL_TIMEOUT: "RAG model timed out",
        AgentErrorCode.NO_INDEXED_DOCUMENTS: "the selected scope has no indexed documents",
    }.get(code, "RAG query failed")
    return ToolResult.failure(
        ToolError(
            code,
            message,
            retryable=code
            not in {AgentErrorCode.RAG_QUERY_FAILED, AgentErrorCode.NO_INDEXED_DOCUMENTS},
        )
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


def _validate_rag_tool_result(result: ToolResult) -> ToolResult:
    """Validate the exact trusted Phase 4 output contract before projection."""

    if not isinstance(result, ToolResult):
        raise ValueError("result must be a ToolResult")
    if result.ok:
        _validate_observation_output(result.output)
    elif result.error is None:
        raise ValueError("failed result requires an error")
    return result


def _rag_observation_projector(result: ToolResult, max_size: int) -> ToolObservation:
    """Project validated RAG output through the C4 sanitizer at its bound."""

    from .observations import (
        MAX_OBSERVATION_TEXT_SIZE,
        ObservationSanitizationError,
        ObservationSanitizer,
    )

    try:
        return ObservationSanitizer(
            max_size=max_size,
            max_text_size=min(MAX_OBSERVATION_TEXT_SIZE, max_size),
        ).sanitize(result, tool_name=ToolName.RAG_QUERY)
    except ObservationSanitizationError as exc:
        raise ValueError("result output could not be safely projected") from exc


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
    if not isinstance(language, str) or language not in set(RAG_ANSWER_LANGUAGES):
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


def _validated_rag_result_to_observation(result: ToolResult) -> ToolObservation:
    """Project one validated RAG result before the C4 observation boundary."""

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


def rag_result_to_observation(result: ToolResult) -> ToolObservation:
    """Project a RAG result through the public C4-safe observation boundary."""

    # Keep this import lazy so the Phase 4 adapter's import remains side-effect
    # free and the sanitizer can use the private validated projection above
    # without a circular import.
    from .observations import ObservationSanitizationError

    try:
        from .observations import MAX_OBSERVATION_SIZE

        return _rag_observation_projector(result, MAX_OBSERVATION_SIZE)
    except (ImportError, ObservationSanitizationError, ValueError) as exc:
        # Preserve the historical ValueError boundary without exposing raw
        # payload or exception details to callers.
        raise ValueError("result output could not be safely projected") from exc


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
                output_validator=_validate_rag_tool_result,
                observation_projector=_rag_observation_projector,
                contract_version=definition.contract_version,
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
