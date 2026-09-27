"""Offline-testable LM Studio adapter for local inference diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from math import ceil
from typing import Any, TypeVar, cast

import httpx2

from aaos.config import AppSettings

from .gateway import (
    InferenceBackendError,
    InferenceConnectionError,
    InferenceContextLimitError,
    InferenceError,
    InferenceProtocolError,
    InferenceTimeoutError,
    ModelUnavailableError,
    UnsupportedInferenceFeatureError,
)
from .models import (
    MAX_ATOMIC_REQUIREMENT_CANDIDATES,
    ChatRequest,
    ChatResponse,
    ModelAvailability,
    TokenUsage,
)
from .telemetry import (
    LocalInferenceTelemetryEvent,
    TelemetrySink,
    emit_local_inference_telemetry,
    safe_backend_category,
    safe_error_code,
    safe_exception_type,
)

_SUPPORTED_MODEL_TYPES = frozenset({"llm", "embedding"})
_ParsedResponse = TypeVar("_ParsedResponse")

_AGENT_PROPOSAL_TRANSPORT_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_agent_proposal_transport_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["tool_call", "terminal_text", "grounded_result_ref"],
                },
                "proposal_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                    "maxLength": 128,
                },
                "call_id": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                    "maxLength": 128,
                },
                "tool_name": {
                    "type": "string",
                    "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$",
                    "maxLength": 128,
                },
                "arguments": {"type": "object"},
                "terminal_text": {"type": "string", "minLength": 1, "maxLength": 16384},
                "grounded_result_ref": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                    "maxLength": 128,
                },
            },
            "required": ["action"],
        },
    },
}

_LEAST_AUTHORITY_PROPOSAL_TRANSPORT_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_least_authority_proposal_transport_v2",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["retrieve", "terminal_text"],
                },
                "terminal_text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 16384,
                },
            },
            "required": ["action"],
        },
    },
}


def _academic_call_schema(
    tool_name: str,
    properties: dict[str, object],
    required: list[str],
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["CALL_TOOL"]},
            "tool_name": {"type": "string", "enum": [tool_name]},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
        "required": ["action", "tool_name", "arguments"],
    }


_ACADEMIC_COURSE_NAME: dict[str, object] = {
    "type": ["string", "null"],
    "maxLength": 512,
}
_ACADEMIC_DECISION_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_read_only_academic_decision_v1",
        "strict": True,
        "schema": {
            "oneOf": [
                _academic_call_schema(
                    "academic.list_courses",
                    {},
                    [],
                ),
                _academic_call_schema(
                    "academic.get_course",
                    {"course_name": {"type": "string", "minLength": 1, "maxLength": 512}},
                    ["course_name"],
                ),
                _academic_call_schema(
                    "academic.list_assignments",
                    {
                        "course_name": _ACADEMIC_COURSE_NAME,
                        "due_window": {
                            "type": ["string", "null"],
                            "enum": ["NEXT_7_DAYS", None],
                        },
                    },
                    ["course_name", "due_window"],
                ),
                _academic_call_schema(
                    "academic.get_assignment",
                    {
                        "assignment_title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 512,
                        },
                        "course_name": _ACADEMIC_COURSE_NAME,
                    },
                    ["assignment_title", "course_name"],
                ),
                _academic_call_schema(
                    "academic.list_documents",
                    {
                        "course_name": _ACADEMIC_COURSE_NAME,
                        "document_title": _ACADEMIC_COURSE_NAME,
                    },
                    ["course_name", "document_title"],
                ),
                _academic_call_schema(
                    "rag.answer",
                    {"course_name": _ACADEMIC_COURSE_NAME},
                    ["course_name"],
                ),
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "enum": ["ANSWER"]},
                        "evidence_refs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {
                                "type": "string",
                                "pattern": "^read-[1-8]$",
                            },
                        },
                    },
                    "required": ["action", "evidence_refs"],
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "enum": ["REFUSE"]},
                        "reason": {
                            "type": "string",
                            "enum": [
                                "UNSUPPORTED_CAPABILITY",
                                "INSUFFICIENT_EVIDENCE",
                                "AMBIGUOUS_ENTITY",
                                "MISSING_ENTITY",
                                "READ_ONLY_ACTION_UNSUPPORTED",
                            ],
                        },
                    },
                    "required": ["action", "reason"],
                },
            ]
        },
    },
}
_CONTEXTUAL_ACADEMIC_DECISION_RESPONSE_FORMAT = cast(
    dict[str, Any], deepcopy(_ACADEMIC_DECISION_RESPONSE_FORMAT)
)
_contextual_academic_json_schema = _CONTEXTUAL_ACADEMIC_DECISION_RESPONSE_FORMAT["json_schema"][
    "schema"
]
for _contextual_branch in _contextual_academic_json_schema["oneOf"]:
    _contextual_action = _contextual_branch.get("properties", {}).get("action", {}).get("enum")
    if _contextual_action == ["CALL_TOOL"]:
        _contextual_arguments = _contextual_branch["properties"]["arguments"]
        _contextual_arguments["properties"]["context_ref"] = {
            "type": "string",
            "enum": ["ctx-1"],
            "maxLength": 5,
        }
        _contextual_arguments["required"].append("context_ref")
_CONTEXTUAL_ACADEMIC_DECISION_RESPONSE_FORMAT["json_schema"]["name"] = (
    "aaos_read_only_academic_decision_contextual_v1"
)


def _study_task_decision_response_format(
    *,
    provider_name: str,
    include_writes: bool,
    contextual: bool,
    task_context: bool = False,
) -> dict[str, Any]:
    response_format = cast(dict[str, Any], deepcopy(_ACADEMIC_DECISION_RESPONSE_FORMAT))
    json_schema = response_format["json_schema"]
    json_schema["name"] = provider_name
    branches = json_schema["schema"]["oneOf"]
    branches.extend(
        (
            _academic_call_schema(
                "study_task.list",
                {
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["OPEN", "COMPLETED", None],
                    },
                    "course_name": _ACADEMIC_COURSE_NAME,
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                ["status", "course_name", "limit"],
            ),
            _academic_call_schema(
                "study_task.get",
                {
                    "task_title": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "maxLength": 500,
                    }
                },
                ["task_title"],
            ),
        )
    )
    if include_writes:
        branches.extend(
            (
                _academic_call_schema(
                    "study_task.create",
                    {
                        "title": {
                            "type": ["string", "null"],
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "assignment_title": {
                            "type": ["string", "null"],
                            "minLength": 1,
                            "maxLength": 512,
                        },
                        "course_name": _ACADEMIC_COURSE_NAME,
                        "assignment_ref": {
                            "type": ["string", "null"],
                            "pattern": "^read-[1-8]$",
                            "maxLength": 6,
                        },
                        "assignment_index": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "priority": {
                            "type": ["string", "null"],
                            "enum": ["LOW", "NORMAL", "HIGH", None],
                        },
                    },
                    [
                        "title",
                        "assignment_title",
                        "course_name",
                        "assignment_ref",
                        "assignment_index",
                        "priority",
                    ],
                ),
                _academic_call_schema(
                    "study_task.update",
                    {
                        "task_title": {
                            "type": ["string", "null"],
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "title": {
                            "type": ["string", "null"],
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "priority": {
                            "type": ["string", "null"],
                            "enum": ["LOW", "NORMAL", "HIGH", None],
                        },
                    },
                    ["task_title", "title", "priority"],
                ),
                _academic_call_schema(
                    "study_task.complete",
                    {
                        "task_title": {
                            "type": ["string", "null"],
                            "minLength": 1,
                            "maxLength": 500,
                        }
                    },
                    ["task_title"],
                ),
            )
        )
    if contextual:
        for branch in branches:
            if branch.get("properties", {}).get("action", {}).get("enum") == ["CALL_TOOL"]:
                tool_name = branch.get("properties", {}).get("tool_name", {}).get("enum")
                if task_context and tool_name == ["study_task.create"]:
                    continue
                arguments = branch["properties"]["arguments"]
                arguments["properties"]["context_ref"] = {
                    "type": "string",
                    "enum": ["ctx-1"],
                    "maxLength": 5,
                }
                arguments["required"].append("context_ref")
    return response_format


_STUDY_TASK_READ_DECISION_RESPONSE_FORMAT = _study_task_decision_response_format(
    provider_name="aaos_study_task_read_decision_v1",
    include_writes=False,
    contextual=False,
)
_STUDY_TASK_READ_DECISION_CONTEXTUAL_RESPONSE_FORMAT = _study_task_decision_response_format(
    provider_name="aaos_study_task_read_decision_contextual_v1",
    include_writes=False,
    contextual=True,
)
_STUDY_TASK_WRITE_DECISION_RESPONSE_FORMAT = _study_task_decision_response_format(
    provider_name="aaos_study_task_write_decision_v1",
    include_writes=True,
    contextual=False,
)
_STUDY_TASK_WRITE_DECISION_CONTEXTUAL_RESPONSE_FORMAT = _study_task_decision_response_format(
    provider_name="aaos_study_task_write_decision_contextual_v1",
    include_writes=True,
    contextual=True,
)
_STUDY_TASK_WRITE_DECISION_TASK_CONTEXTUAL_RESPONSE_FORMAT = _study_task_decision_response_format(
    provider_name="aaos_study_task_write_decision_task_contextual_v1",
    include_writes=True,
    contextual=True,
    task_context=True,
)


def _calendar_proposal_decision_response_format(
    *,
    provider_name: str,
    contextual: bool,
    include_study_task_reads: bool,
) -> dict[str, Any]:
    response_format = cast(
        dict[str, Any],
        deepcopy(
            _STUDY_TASK_READ_DECISION_RESPONSE_FORMAT
            if include_study_task_reads
            else _ACADEMIC_DECISION_RESPONSE_FORMAT
        ),
    )
    json_schema = response_format["json_schema"]
    json_schema["name"] = provider_name
    branches = json_schema["schema"]["oneOf"]
    branches.append(
        _academic_call_schema(
            "calendar.propose_create_event",
            {
                "source_ref": {
                    "type": ["string", "null"],
                    "enum": [*(f"read-{index}" for index in range(1, 9)), None],
                    "maxLength": 6,
                },
                "source_index": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            ["source_ref", "source_index"],
        )
    )
    if contextual:
        for branch in branches:
            if branch.get("properties", {}).get("action", {}).get("enum") == ["CALL_TOOL"]:
                arguments = branch["properties"]["arguments"]
                arguments["properties"]["context_ref"] = {
                    "type": "string",
                    "enum": ["ctx-1"],
                    "maxLength": 5,
                }
                arguments["required"].append("context_ref")
    return response_format


_CALENDAR_PROPOSAL_DECISION_RESPONSE_FORMAT = _calendar_proposal_decision_response_format(
    provider_name="aaos_calendar_proposal_decision_v1",
    contextual=False,
    include_study_task_reads=False,
)
_CONTEXTUAL_CALENDAR_PROPOSAL_DECISION_RESPONSE_FORMAT = (
    _calendar_proposal_decision_response_format(
        provider_name="aaos_calendar_proposal_decision_contextual_v1",
        contextual=True,
        include_study_task_reads=False,
    )
)
_CALENDAR_PROPOSAL_STUDY_TASK_READ_DECISION_RESPONSE_FORMAT = (
    _calendar_proposal_decision_response_format(
        provider_name="aaos_calendar_proposal_study_task_read_decision_v1",
        contextual=False,
        include_study_task_reads=True,
    )
)
_CONTEXTUAL_CALENDAR_PROPOSAL_STUDY_TASK_READ_DECISION_RESPONSE_FORMAT = (
    _calendar_proposal_decision_response_format(
        provider_name="aaos_calendar_proposal_study_task_read_decision_contextual_v1",
        contextual=True,
        include_study_task_reads=True,
    )
)

_GROUNDED_RAG_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_grounded_rag_answer_v1",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ANSWER", "PARTIAL_ANSWER", "INSUFFICIENT_EVIDENCE"],
                },
                "answer_text": {"type": "string", "minLength": 1},
                "language": {"type": "string", "enum": ["en", "ar"]},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "claim_id": {"type": "string", "minLength": 1},
                            "text": {"type": "string", "minLength": 1},
                            "evidence_ids": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "minItems": 1,
                            },
                        },
                        "required": ["claim_id", "text", "evidence_ids"],
                    },
                },
            },
            "required": ["status", "answer_text", "language", "claims"],
        },
    },
}

_SEMANTIC_COVERAGE_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_semantic_coverage_v1",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["SUPPORTED", "PARTIAL", "UNSUPPORTED"],
                },
                "unsupported_aspects": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 4,
                },
            },
            "required": ["status", "unsupported_aspects"],
        },
    },
}

_REQUIREMENT_DECOMPOSITION_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_requirement_decomposition_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "requirement_text": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 240,
                            }
                        },
                        "required": ["requirement_text"],
                    },
                }
            },
            "required": ["requirements"],
        },
    },
}

_REQUIREMENT_COVERAGE_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "aaos_requirement_coverage_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "requirement_id": {
                                "type": "string",
                                "pattern": "^r[1-6]$",
                            },
                            "support": {
                                "type": "string",
                                "enum": ["SUPPORTED", "UNSUPPORTED"],
                            },
                            "evidence_ids": {
                                "type": "array",
                                "maxItems": 8,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                },
                            },
                        },
                        "required": ["requirement_id", "support", "evidence_ids"],
                    },
                }
            },
            "required": ["requirements"],
        },
    },
}

_STRUCTURED_RESPONSE_FORMATS = {
    "agent_proposal_transport": _AGENT_PROPOSAL_TRANSPORT_RESPONSE_FORMAT,
    "least_authority_proposal_transport": _LEAST_AUTHORITY_PROPOSAL_TRANSPORT_RESPONSE_FORMAT,
    "read_only_academic_decision": _ACADEMIC_DECISION_RESPONSE_FORMAT,
    "read_only_academic_decision_contextual": _CONTEXTUAL_ACADEMIC_DECISION_RESPONSE_FORMAT,
    "study_task_read_decision": _STUDY_TASK_READ_DECISION_RESPONSE_FORMAT,
    "study_task_read_decision_contextual": _STUDY_TASK_READ_DECISION_CONTEXTUAL_RESPONSE_FORMAT,
    "study_task_write_decision": _STUDY_TASK_WRITE_DECISION_RESPONSE_FORMAT,
    "study_task_write_decision_contextual": _STUDY_TASK_WRITE_DECISION_CONTEXTUAL_RESPONSE_FORMAT,
    "study_task_write_decision_task_contextual": (
        _STUDY_TASK_WRITE_DECISION_TASK_CONTEXTUAL_RESPONSE_FORMAT
    ),
    "calendar_proposal_decision": _CALENDAR_PROPOSAL_DECISION_RESPONSE_FORMAT,
    "calendar_proposal_decision_contextual": (
        _CONTEXTUAL_CALENDAR_PROPOSAL_DECISION_RESPONSE_FORMAT
    ),
    "calendar_proposal_study_task_read_decision": (
        _CALENDAR_PROPOSAL_STUDY_TASK_READ_DECISION_RESPONSE_FORMAT
    ),
    "calendar_proposal_study_task_read_decision_contextual": (
        _CONTEXTUAL_CALENDAR_PROPOSAL_STUDY_TASK_READ_DECISION_RESPONSE_FORMAT
    ),
    "agent_proposal": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_agent_proposal_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                        "maxLength": 128,
                    },
                    "tool_call": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {
                                "type": "string",
                                "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,127}$",
                                "maxLength": 128,
                            },
                            "arguments": {"type": "object"},
                            "call_id": {
                                "type": "string",
                                "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                                "maxLength": 128,
                            },
                        },
                        "required": ["name", "arguments"],
                    },
                    "terminal_text": {"type": "string", "minLength": 1, "maxLength": 16384},
                    "grounded_result_ref": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$",
                        "maxLength": 128,
                    },
                },
                "oneOf": [
                    {"required": ["tool_call"]},
                    {"required": ["terminal_text"]},
                    {"required": ["grounded_result_ref"]},
                ],
            },
        },
    },
    "grounded_answer": _GROUNDED_RAG_RESPONSE_FORMAT,
    "semantic_coverage": _SEMANTIC_COVERAGE_RESPONSE_FORMAT,
    "requirement_decomposition": _REQUIREMENT_DECOMPOSITION_RESPONSE_FORMAT,
    "requirement_coverage": _REQUIREMENT_COVERAGE_RESPONSE_FORMAT,
    "atomic_requirement_decomposition": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_atomic_requirement_decomposition_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["OK", "CANDIDATE_LIMIT_REACHED"]},
                    "requirements": {
                        "type": "array",
                        "maxItems": MAX_ATOMIC_REQUIREMENT_CANDIDATES,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "requirement_text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 240,
                                }
                            },
                            "required": ["requirement_text"],
                        },
                    },
                },
                "required": ["status", "requirements"],
            },
        },
    },
    "evidence_spans": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_evidence_spans_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "requirements": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "requirement_id": {"type": "string", "pattern": "^r[1-6]$"},
                                "spans": {
                                    "type": "array",
                                    "maxItems": 4,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "evidence_id": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 128,
                                            },
                                            "quote": {
                                                "type": "string",
                                                "minLength": 1,
                                                "maxLength": 400,
                                            },
                                        },
                                        "required": ["evidence_id", "quote"],
                                    },
                                },
                            },
                            "required": ["requirement_id", "spans"],
                        },
                    }
                },
                "required": ["requirements"],
            },
        },
    },
    "evidence_spans_single_requirement": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_evidence_spans_single_requirement_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "spans": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                },
                                "quote": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 400,
                                },
                            },
                            "required": ["evidence_id", "quote"],
                        },
                    }
                },
                "required": ["spans"],
            },
        },
    },
    "evidence_segments_single_requirement": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_evidence_segments_single_requirement_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "segment_ids": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "string",
                            "pattern": "^s[0-9]{3,}$",
                        },
                    }
                },
                "required": ["segment_ids"],
            },
        },
    },
    "requirement_entailment": {
        "type": "json_schema",
        "json_schema": {
            "name": "aaos_requirement_entailment_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"result": {"type": "string", "enum": ["ENTAILED", "NOT_ENTAILED"]}},
                "required": ["result"],
            },
        },
    },
}

_SCHEMA_PROMPT_TOKEN_RESERVE = 128


class LMStudioGateway:
    """Own one async client and report the configured model's loaded state."""

    def __init__(
        self,
        settings: AppSettings,
        transport: httpx2.AsyncBaseTransport,
        diagnostic_sink: TelemetrySink | None = None,
    ) -> None:
        """Take exclusive ownership of a dedicated async transport."""
        self._settings = settings
        self._client = httpx2.AsyncClient(
            transport=transport,
            base_url=settings.lm_studio_base_url,
            timeout=settings.lm_studio_request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._diagnostic_sink = diagnostic_sink or emit_local_inference_telemetry
        self._closed = False

    async def check_availability(self) -> ModelAvailability:
        """Query native inventory once and validate all data before reporting it."""
        operation = "availability"
        model = self._settings.lm_studio_model
        response = await self._send(
            method="GET",
            path="/api/v1/models",
            operation=operation,
        )
        return _parse_success_response(
            response,
            operation=operation,
            model=model,
            parser=lambda payload: _parse_availability(payload, configured_model=model),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        """Send one plain chat request and return validated visible output."""
        operation = "generation"
        model = self._settings.lm_studio_model

        if self._closed:
            raise InferenceError("LM Studio gateway is closed") from None
        if request.reasoning:
            raise UnsupportedInferenceFeatureError("Reasoning is not supported in Phase 0E")
        if (
            request.max_output_tokens is not None
            and request.max_output_tokens > self._settings.lm_studio_context_length
        ):
            raise ValueError("max_output_tokens exceeds the configured context length")

        request_payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": False,
            "reasoning_effort": "none",
        }
        if request.temperature is not None:
            request_payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            request_payload["max_tokens"] = request.max_output_tokens
            estimated_prompt_tokens = _estimate_prompt_tokens(request)
            if (
                estimated_prompt_tokens + request.max_output_tokens
                > self._settings.lm_studio_context_length
            ):
                raise InferenceContextLimitError(
                    "LM Studio request exceeds the configured context window"
                )
        if request.structured_output == "json_schema":
            request_payload["response_format"] = deepcopy(
                _STRUCTURED_RESPONSE_FORMATS[request.structured_output_schema]
            )

        response = await self._send(
            method="POST",
            path="/v1/chat/completions",
            operation=operation,
            request_payload=request_payload,
            timeout_seconds=request.request_timeout_seconds,
        )
        return _parse_success_response(
            response,
            operation=operation,
            model=model,
            parser=_parse_generation,
        )

    async def _send(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        request_payload: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> httpx2.Response:
        """Execute one request and expose only fixed AAOS failures."""
        model = self._settings.lm_studio_model
        if self._closed:
            raise InferenceError("LM Studio gateway is closed") from None

        translated_error: InferenceError
        try:
            if request_payload is None:
                if timeout_seconds is None:
                    response = await self._client.request(method, path)
                else:
                    response = await self._client.request(method, path, timeout=timeout_seconds)
            else:
                if timeout_seconds is None:
                    response = await self._client.request(method, path, json=request_payload)
                else:
                    response = await self._client.request(
                        method,
                        path,
                        json=request_payload,
                        timeout=timeout_seconds,
                    )
        except httpx2.TimeoutException:
            translated_error = InferenceTimeoutError(
                f"LM Studio request timed out during {operation} for model '{model}'"
            )
        except httpx2.RequestError:
            translated_error = InferenceConnectionError(
                f"LM Studio connection failed during {operation} for model '{model}'"
            )
        else:
            if not response.is_success:
                try:
                    _raise_backend_error(
                        response,
                        operation=operation,
                        model=model,
                    )
                except InferenceError as error:
                    self._emit_failure(
                        operation,
                        error,
                        http_status=response.status_code,
                        backend_category=_backend_category(response, error),
                    )
                    raise
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="INFERENCE_REQUEST",
                    model_identifier=model,
                    model_role="answer",
                    lifecycle_state="completed",
                    http_status=response.status_code,
                    backend_category="success",
                )
            )
            return response

        self._emit_failure(operation, translated_error)
        raise translated_error from None

    def _emit_failure(
        self,
        operation: str,
        error: BaseException,
        *,
        http_status: int | None = None,
        backend_category: str | None = None,
    ) -> None:
        self._emit(
            LocalInferenceTelemetryEvent(
                operation="INFERENCE_REQUEST",
                model_identifier=self._settings.lm_studio_model,
                model_role="answer",
                lifecycle_state=f"failed:{operation}",
                exception_type=safe_exception_type(error),
                safe_error_code=safe_error_code(error),
                http_status=http_status,
                backend_category=backend_category or safe_backend_category(error),
            )
        )

    def _emit(self, event: LocalInferenceTelemetryEvent) -> None:
        try:
            self._diagnostic_sink(event)
        except Exception:
            return

    async def aclose(self) -> None:
        """Close the exclusively owned client at most once."""
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()


def _estimate_prompt_tokens(request: ChatRequest) -> int:
    """Estimate prompt usage conservatively without model/tokenizer activity."""
    content_bytes = sum(len(message.content.encode("utf-8")) for message in request.messages)
    message_overhead = 16 * len(request.messages)
    schema_overhead = (
        _SCHEMA_PROMPT_TOKEN_RESERVE if request.structured_output == "json_schema" else 0
    )
    return ceil(content_bytes / 4) + message_overhead + schema_overhead


def _raise_backend_error(
    response: httpx2.Response,
    *,
    operation: str,
    model: str,
) -> None:
    if operation == "generation":
        backend_error = _read_structured_backend_error(response)
        if backend_error is not None:
            if (
                backend_error.get("code") == "model_not_found"
                or backend_error.get("type") == "model_not_found"
            ):
                raise ModelUnavailableError(
                    f"LM Studio model '{model}' is unavailable during generation"
                ) from None
            if backend_error.get("param") == "reasoning_effort":
                raise UnsupportedInferenceFeatureError(
                    "LM Studio does not support required reasoning-off generation "
                    f"for model '{model}'"
                ) from None

    raise InferenceBackendError(
        f"LM Studio backend failed during {operation} for model '{model}' "
        f"(status {response.status_code})"
    ) from None


def _read_structured_backend_error(
    response: httpx2.Response,
) -> dict[Any, Any] | None:
    try:
        payload: object = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    backend_error = payload.get("error")
    return backend_error if isinstance(backend_error, dict) else None


def _backend_category(response: httpx2.Response, error: InferenceError) -> str:
    """Classify a backend failure without retaining its response body."""
    if isinstance(error, ModelUnavailableError):
        return "model_not_found"
    if isinstance(error, UnsupportedInferenceFeatureError):
        return "unsupported_feature"
    if isinstance(error, InferenceBackendError):
        return f"http_{response.status_code}"
    return safe_backend_category(error)


def _parse_success_response(
    response: httpx2.Response,
    *,
    operation: str,
    model: str,
    parser: Callable[[object], _ParsedResponse],
) -> _ParsedResponse:
    try:
        payload: object = response.json()
        parsed_response = parser(payload)
    except (TypeError, ValueError):
        translated_error = InferenceProtocolError(
            f"LM Studio returned invalid data during {operation} for model '{model}'"
        )
    else:
        return parsed_response

    raise translated_error from None


def _parse_availability(payload: object, *, configured_model: str) -> ModelAvailability:
    if not isinstance(payload, dict):
        raise ValueError("inventory must be an object")

    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("models must be a list")

    available_models: list[str] = []
    loaded_context_lengths: list[int] = []

    for native_model in models:
        if not isinstance(native_model, dict):
            raise ValueError("model must be an object")

        key = native_model.get("key")
        model_type = native_model.get("type")
        loaded_instances = native_model.get("loaded_instances")

        if not isinstance(key, str) or not key.strip():
            raise ValueError("model key must be a non-empty string")
        if not isinstance(model_type, str) or model_type not in _SUPPORTED_MODEL_TYPES:
            raise ValueError("model type is unsupported")
        if not isinstance(loaded_instances, list):
            raise ValueError("loaded_instances must be a list")

        instance_context_lengths = _parse_loaded_context_lengths(loaded_instances)
        _validate_reasoning_capability(native_model)
        available_models.append(key)

        if key == configured_model and model_type == "llm":
            loaded_context_lengths.extend(instance_context_lengths)

    context_tuple = tuple(loaded_context_lengths)
    return ModelAvailability(
        reachable=True,
        available=bool(context_tuple),
        configured_model=configured_model,
        available_models=tuple(available_models),
        loaded_context_lengths=context_tuple,
    )


def _parse_generation(payload: object) -> ChatResponse:
    if not isinstance(payload, dict):
        raise ValueError("generation response must be an object")

    model = payload.get("model")
    choices = payload.get("choices")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(choices, list) or not choices:
        raise ValueError("choices must be a non-empty list")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    if message.get("role") != "assistant":
        raise ValueError("message role must be assistant")
    if "tool_calls" in message:
        tool_calls = message["tool_calls"]
        if not isinstance(tool_calls, list) or tool_calls:
            raise ValueError("tool calls are not supported")
    for reasoning_field in ("reasoning", "reasoning_content"):
        reasoning_value = message.get(reasoning_field)
        if reasoning_value not in (None, ""):
            raise ValueError("reasoning content is not supported")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")

    usage = _parse_usage(payload.get("usage")) if "usage" in payload else None
    return ChatResponse(content=content, model_identifier=model, usage=usage)


def _parse_usage(payload: object) -> TokenUsage | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("usage must be an object")
    prompt_tokens = _parse_token_count(payload.get("prompt_tokens"))
    completion_tokens = _parse_token_count(payload.get("completion_tokens"))
    total_tokens = _parse_token_count(payload.get("total_tokens"))
    return TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _parse_token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    return value


def _parse_loaded_context_lengths(loaded_instances: list[Any]) -> list[int]:
    context_lengths: list[int] = []
    for loaded_instance in loaded_instances:
        if not isinstance(loaded_instance, dict):
            raise ValueError("loaded instance must be an object")
        config = loaded_instance.get("config")
        if not isinstance(config, dict):
            raise ValueError("loaded instance config must be an object")
        context_length = config.get("context_length")
        if (
            not isinstance(context_length, int)
            or isinstance(context_length, bool)
            or context_length <= 0
        ):
            raise ValueError("context_length must be a positive integer")
        context_lengths.append(context_length)
    return context_lengths


def _validate_reasoning_capability(native_model: dict[Any, Any]) -> None:
    if "capabilities" not in native_model:
        return

    capabilities = native_model["capabilities"]
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    if "reasoning" not in capabilities:
        return

    reasoning = capabilities["reasoning"]
    if not isinstance(reasoning, dict):
        raise ValueError("reasoning capability must be an object")

    allowed_options = reasoning.get("allowed_options")
    default = reasoning.get("default")
    if (
        not isinstance(allowed_options, list)
        or not allowed_options
        or any(not isinstance(option, str) or not option for option in allowed_options)
    ):
        raise ValueError("reasoning allowed_options must contain strings")
    if not isinstance(default, str) or default not in allowed_options:
        raise ValueError("reasoning default must be an allowed option")
