"""Trusted, bounded projection of tool results into model-visible data.

The agent model receives observations only after this module has projected the
trusted :class:`ToolResult` into an allowlisted, JSON-compatible
``ToolObservation``.  No value in an observation grants permission, changes a
registry, or mutates task state; instruction-like document text is data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .contracts import (
    MAX_AGENT_TEXT_CHARACTERS,
    AgentErrorCode,
    ToolName,
    ToolObservation,
    ToolResult,
    canonical_json,
)
from .events import Phase8ActivityEvent, Phase8ActivityKind, Phase8ActivityStatus
from .phase4_adapter import _validated_rag_result_to_observation

MAX_OBSERVATION_SIZE: Final[int] = MAX_AGENT_TEXT_CHARACTERS
"""Maximum canonical serialized size of one model-visible observation."""

MAX_OBSERVATION_TEXT_SIZE: Final[int] = 8_192
"""Maximum generic text field before deterministic truncation."""

MAX_OBSERVATION_IDENTIFIER_SIZE: Final[int] = 256
"""Maximum size of bounded identifiers retained in an observation."""

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_FILE_URI_PATH = re.compile(r"(?i)\bfile://[^\s\r\n\"'<>]+")
_UNC_SLASH_PATH = re.compile(r"(?<![A-Za-z0-9:/])//(?=[^./\s\"'<>]+/)[^\s\r\n\"'<>]+")
_UNC_DOTTED_SHARE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9:/])//(?=[^/\s\"'<>]+\.[^/\s\"'<>]+/"
    r"(?:share|users|home|data|documents|private|credentials?|secrets?|database|rag)(?:/|$))"
    r"[^\s\r\n\"'<>]+"
)
_UNC_DOTTED_SENSITIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9:/])//(?=[^/\s\"'<>]+\.[^/\s\"'<>]+/"
    r"(?:[^/\r\n\"'<>]+/)*(?:credentials?|secrets?|tokens?|passwords?|auth|"
    r"database|documents|rag|cookies?|config(?:uration)?|private)(?:[/ .,:;!?)}\t\r\n]|$))"
    r"[^\s\r\n\"'<>]+"
)
_UNC_ROOT = re.compile(r"(?<![A-Za-z0-9:/])//[^./\s\"'<>]+(?=$|[ \t\r\n])")
_ENV_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(?:~|\$env:[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%)"
    r"[\\/][^\s\r\n\"'<>]+"
)
_WINDOWS_SENSITIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    r"(?:[^\\/'\"<>\r\n]*[\\/])*"
    r"[^\\/'\"<>\r\n]*?(?:credentials?|secrets?|tokens?|passwords?|auth|database|documents|rag|cookies?|config(?:uration)?|private)"
    r"[^\\/'\"<>\r\n]*?(?=[\"'`.,;:!?)}]|$|\r?\n|[ \t])"
)
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    r"(?:[^\\/'\"<>\r\n]*[\\/])*"
    r"[^\s\\/'\"<>]+(?:[ \t]+[^\s\\/'\"<>]+)*?\.[A-Za-z0-9]{1,16}"
    r"[\\/]?"
)
_WINDOWS_DIRECTORY_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    r"(?:[^\\/'\"<>\r\n]+[\\/])+"
    r"(?=[\"'`]|$|[ \t\r\n])"
)
_POSIX_SENSITIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9/])/(?!/)"
    r"(?:[^/'\"<>\r\n]*[/])*"
    r"[^/'\"<>\r\n]*?(?:credentials?|secrets?|tokens?|passwords?|auth|database|documents|rag|cookies?|config(?:uration)?|private)"
    r"[^/'\"<>\r\n]*?(?=[\"'`.,;:!?)}]|$|\r?\n|[ \t])"
)
_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9/])/(?!/)(?:[^/'\"<>\r\n]*[/])*"
    r"[^\s/'\"<>]+(?:[ \t]+[^\s/'\"<>]+)*?\.[A-Za-z0-9]{1,16}"
    r"/?"
)
_POSIX_DIRECTORY_PATH = re.compile(
    r"(?<![A-Za-z0-9/])/(?!/)(?:[^/'\"<>\r\n]+[/])+"
    r"(?=[\"'`]|$|[ \t\r\n])"
)
_RELATIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?:\.\.?[\\/]|[A-Za-z0-9_-]+[\\/])"
    r"(?:[^\\/'\"<>\r\n]*[\\/])*"
    r"[^\s\\/'\"<>]+(?:[ \t]+[^\s\\/'\"<>]+)*?\.[A-Za-z0-9]{1,16}"
)
_RELATIVE_SENSITIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])"
    r"(?:"
    r"(?:\.\.?[\\/]|[A-Za-z0-9_-]+[\\/])+"
    r"(?:credentials?|secrets?|tokens?|passwords?|auth|database|documents|rag|cookies?|config(?:uration)?|private)"
    r"(?:[\\/][^\\/'\"<>\r\n]+)*"
    r"|"
    r"(?:credentials?|secrets?|tokens?|passwords?|auth|database|documents|rag|cookies?|config(?:uration)?|private)"
    r"(?:[\\/][^\\/'\"<>\r\n]+)+"
    r")"
    r"(?=[\"'`.,;:!?)}]|$|\r?\n|[ \t])"
)
_DOT_RELATIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])\.[A-Za-z0-9_-]+[\\/]"
    r"(?:[^\\/'\"<>\r\n]+[\\/])*[^\s\\/'\"<>]+"
)
_RELATIVE_DOTTED_SENSITIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]+[\\/]"
    r"(?:[^\\/'\"<>\r\n]+[\\/])*"
    r"(?:credentials?|secrets?|tokens?|passwords?|auth|database|documents|rag|"
    r"cookies?|config(?:uration)?|private|signing[_ -]?key)(?:[\\/]|[ .,:;!?)}\t\r\n]|$)"
    r"[^\r\n\"'<>]*"
)
_RELATIVE_DIRECTORY_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?:\.\.?[\\/]|[A-Za-z0-9_-]+[\\/])"
    r"(?:[^\\/'\"<>\r\n]+[\\/])*"
    r"(?=[\"'`]|$|[ \t\r\n])"
)
_RELATIVE_PATH_MARKER = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?!(?:\d+[\\/])+\d+(?:[\"'`.,;:!?)}]|$))"
    r"(?:"
    r"(?:\.\.?[\\/]|(?:src|users|home|docs|tmp|var|app|private)[\\/])"
    r"(?:[^\\/'\"<>\r\n]+[\\/])+[^\s\\/'\"<>]+"
    r"|(?:[A-Za-z0-9_-]+[\\/]){2}[^\s\\/'\"<>]+"
    r")"
)
_AMBIGUOUS_SPACED_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:\\-])(?:\bfile://|//|~[\\/]|"
    r"\$(?:env:[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)[\\/]|"
    r"%[A-Za-z_][A-Za-z0-9_]*%[\\/]|"
    r"\.[A-Za-z0-9_-]+[\\/]|(?:src|users|home|docs|tmp|var|app|private)[\\/])"
    r"[^\r\n\"'<>]*\s+[^\r\n\"'<>]*[\\/][^\r\n\"'<>]*"
)
_AMBIGUOUS_SPACED_RELATIVE = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:\\-])(?:src|docs|tmp|var|app|private)/"
    r"[^\r\n\"'<>]*\s+[^\r\n\"'<>]*"
)
_SPACED_DRIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)"
    r"[^\r\n\"'<>]*\s+[^\r\n\"'<>]*[\\/][^\r\n\"'<>]*"
)
_SPACED_POSIX_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9/])/(?:[^/\r\n\"'<>]+/)*"
    r"[^/\r\n\"'<>]*\s+[^/\r\n\"'<>]*/[^\r\n\"'<>]*"
)
_SPACED_DOTTED_RELATIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]+[\\/]"
    r"[^\r\n\"'<>]*\s+[^\r\n\"'<>]*[\\/][^\r\n\"'<>]*"
)
_SPACED_SENSITIVE_RELATIVE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:\\-])(?:credentials?|secrets?|tokens?|passwords?|auth|"
    r"database|documents|rag|cookies?|config(?:uration)?|private)[^\r\n\"'<>]*\s+"
    r"[^\r\n\"'<>]*[\\/][^\r\n\"'<>]*"
)
_RELATIVE_BARE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_./:-])(?!(?:\d+[\\/])+\d+(?:[\"'`.,;:!?)}]|$))"
    r"(?:"
    r"(?:\.\.?[\\/]|(?:src|users|home|docs|tmp|var|app|private)[\\/])"
    r"[^\\/'\"<>\s]+(?:[\\/][^\\/'\"<>\s]+)*"
    r"|(?:[A-Za-z0-9_-]+[\\/]){2}[^\\/'\"<>\s]+"
    r")"
    r"(?=[\"'`.,;:!?)}]|$)"
)
_WINDOWS_PATH_MARKER = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)(?=[^\r\n<>\"']+)")
_POSIX_PATH_MARKER = re.compile(r"(?<![A-Za-z0-9/])/(?!/)(?=[^/\s\"'<>])")
_DRIVE_ROOT = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_AUTH_HEADER = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|x-api-key|x-auth-token)\s*:\s*"
    r"(?:bearer|basic)?\s*[^\s,;]+"
)
_COOKIE_HEADER = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*[^\s,;]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Za-z0-9_.-]*(?:api[_ .-]?key|access[_ .-]?token|refresh[_ .-]?token|"
    r"oauth[_ .-]?token|session[_ .-]?token|auth(?:orization)?[_ .-]?(?:header|token|state)?|"
    r"cookie|proxy-authorization|x-api-key|x-auth-token|authorization|client[_ .-]?secret|"
    r"private[_ .-]?key|signing[_ .-]?key|encryption[_ .-]?key|token|password|secret|"
    r"credential(?:s)?)[A-Za-z0-9_.-]*\b\s*[:=]\s*[^\n]+"
)
_QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"(?is)[\"'][A-Za-z0-9_.-]*(?:api[_ .-]?key|access[_ .-]?token|refresh[_ .-]?token|"
    r"oauth[_ .-]?token|session[_ .-]?token|auth(?:orization)?[_ .-]?(?:header|token|state)?|"
    r"cookie|proxy-authorization|x-api-key|x-auth-token|authorization|client[_ .-]?secret|"
    r"private[_ .-]?key|signing[_ .-]?key|encryption[_ .-]?key|token|password|secret|"
    r"credential(?:s)?)[A-Za-z0-9_.-]*[\"']\s*[:=]\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,}\]]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_CLI_SECRET_FLAG = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])--(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|"
    r"auth[-_ ]?token|password|secret|credential)\s+[^\s,;]+"
)
_BARE_AUTH_HEADER = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s+(?:bearer|basic)\s+[^\s,;]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)\b([A-Za-z][A-Za-z0-9+.-]*)://[^/\s:@]*:[^/\s]+@")
_PROTOCOL_RELATIVE_CREDENTIALS = re.compile(r"(?i)(?<![A-Za-z0-9:/])//[^/\s:@]*:[^/\s]+@")
_TRACEBACK = re.compile(r"(?is)traceback\s*\(most recent call last\):.*")
_EXCEPTION_REPR = re.compile(r"(?is)\b(?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\s*\([^)]*\)")
_EXCEPTION_LINE = re.compile(r"(?im)^\s*[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\s*:[^\n]*")
_NESTED_EXCEPTION_OBJECT = re.compile(
    r"(?is)\{[^{}]{0,512}[\"'](?:type|class|exception|"
    r"(?:exception|error)[_-]?(?:type|class)?)[\"']\s*[:=]\s*"
    r"[\"'](?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)|Error|Exception)[\"'][^{}]{0,1024}\}"
)
_PEM_PRIVATE_KEY = re.compile(
    r"(?is)-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----"
)
_STRUCTURED_DOUBLE_EXCEPTION = re.compile(
    r"(?is)([\"\'](?:error|exception|trace|message|detail)[\"\']\s*[:=]\s*)"
    r"\"(?:\\.|[^\"\\])*\""
)
_STRUCTURED_SINGLE_EXCEPTION = re.compile(
    r"(?is)([\"'](?:error|exception|trace|message|detail)[\"']\s*[:=]\s*)"
    r"'(?:\\.|[^'\\])*'"
)
_INLINE_EXCEPTION = re.compile(
    r"(?i)\b(?:Error|Exception|[A-Z][A-Za-z0-9_]*(?:Error|Exception))\s*:\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^}\]]*)"
)
_PATH_TRAILING_PUNCTUATION = ".,;:!?)]}"

_GENERIC_FIELDS: dict[str, frozenset[str]] = {
    "read_task_state": frozenset({"task_id", "state", "summary"}),
    "write_task_state": frozenset({"task_id", "state", "summary", "status"}),
    "complete_task": frozenset({"task_id", "state", "summary", "status"}),
}

_SAFE_ERROR_MESSAGES: dict[AgentErrorCode, str] = {
    AgentErrorCode.INVALID_PROPOSAL: "agent proposal was invalid",
    AgentErrorCode.INVALID_TOOL_ARGUMENTS: "tool arguments were invalid",
    AgentErrorCode.PERMISSION_DENIED: "tool permission was denied",
    AgentErrorCode.UNKNOWN_TOOL: "requested tool is not available",
    AgentErrorCode.TOOL_NOT_EXECUTABLE: "requested tool is not executable",
    AgentErrorCode.TOOL_TIMEOUT: "tool execution timed out",
    AgentErrorCode.TOOL_FAILED: "tool execution failed",
    AgentErrorCode.HANDLER_EXECUTION_FAILED: "tool execution failed",
    AgentErrorCode.RAG_QUERY_FAILED: "RAG query failed",
    AgentErrorCode.MODEL_TIMEOUT: "local model timed out",
    AgentErrorCode.MODEL_UNAVAILABLE: "local model is unavailable",
}


class ObservationSanitizationError(ValueError):
    """Safe, stable error raised when a result cannot cross the data boundary."""

    code: Final[AgentErrorCode] = AgentErrorCode.OBSERVATION_SANITIZATION_FAILED

    def __init__(self, message: str = "tool observation could not be sanitized") -> None:
        # Never include a raw result, exception representation, path, or
        # secret in this public/model-adjacent error.
        del message
        super().__init__("tool observation could not be sanitized")


@dataclass(frozen=True, slots=True)
class ObservationAudit:
    """Bounded metadata for trusted local audit logs.

    This object intentionally contains no observation text or raw result.
    """

    tool_name: str
    result_status: str
    sanitization_status: str
    input_size: int
    output_size: int
    truncated: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        for name in ("tool_name", "result_status", "sanitization_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a safe identifier")
        for name in ("input_size", "output_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.truncated, bool):
            raise TypeError("truncated must be a boolean")
        if self.error_code is not None and not _SAFE_IDENTIFIER.fullmatch(self.error_code):
            raise ValueError("error_code must be a safe identifier")

    def to_dict(self) -> dict[str, object]:
        return {
            "error_code": self.error_code,
            "input_size": self.input_size,
            "output_size": self.output_size,
            "result_status": self.result_status,
            "sanitization_status": self.sanitization_status,
            "tool_name": self.tool_name,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class SanitizedObservation:
    """Observation plus safe metadata retained by trusted application code."""

    observation: ToolObservation
    audit: ObservationAudit

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ToolObservation):
            raise TypeError("observation must be a ToolObservation")
        if not isinstance(self.audit, ObservationAudit):
            raise TypeError("audit must be an ObservationAudit")


class ObservationSanitizer:
    """Project trusted results through a closed, deterministic observation schema."""

    def __init__(
        self,
        *,
        max_size: int = MAX_OBSERVATION_SIZE,
        max_text_size: int = MAX_OBSERVATION_TEXT_SIZE,
    ) -> None:
        if (
            isinstance(max_size, bool)
            or not isinstance(max_size, int)
            or max_size <= 0
            or max_size > MAX_OBSERVATION_SIZE
        ):
            raise ValueError("max_size must be a positive observation bound")
        if (
            isinstance(max_text_size, bool)
            or not isinstance(max_text_size, int)
            or max_text_size <= 0
            or max_text_size > max_size
        ):
            raise ValueError("max_text_size must be a positive text bound")
        self._max_size = max_size
        self._max_text_size = max_text_size

    @property
    def max_size(self) -> int:
        return self._max_size

    def sanitize(self, result: ToolResult, *, tool_name: ToolName | str) -> ToolObservation:
        return self.sanitize_with_audit(result, tool_name=tool_name).observation

    def sanitize_with_audit(
        self, result: ToolResult, *, tool_name: ToolName | str
    ) -> SanitizedObservation:
        normalized_name = _normalize_tool_name(tool_name)
        if not isinstance(result, ToolResult):
            raise ObservationSanitizationError()
        try:
            source_json = canonical_json(result.to_dict())
            if normalized_name == ToolName.RAG_QUERY.value:
                observation = self._rag_observation(result)
                truncated = False
            elif normalized_name.startswith("academic.") and result.ok:
                observation, truncated = self._academic_observation(result, normalized_name)
            elif result.ok:
                observation, truncated = self._generic_observation(result, normalized_name)
            else:
                observation = self._error_observation(result, normalized_name)
                truncated = False
            observation, fitted_truncated = self._fit_observation(observation, truncated=truncated)
            truncated = truncated or fitted_truncated
            output_size = len(canonical_json(observation.to_dict()))
            error_code = dict(observation.fields).get("error_code")
            audit = ObservationAudit(
                tool_name=normalized_name,
                result_status=observation.status,
                sanitization_status="SANITIZED",
                input_size=len(source_json),
                output_size=output_size,
                truncated=truncated,
                error_code=error_code if isinstance(error_code, str) else None,
            )
            return SanitizedObservation(observation, audit)
        except ObservationSanitizationError:
            raise
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            raise ObservationSanitizationError() from exc

    def _academic_observation(
        self, result: ToolResult, tool_name: str
    ) -> tuple[ToolObservation, bool]:
        output = result.output
        if not isinstance(output, Mapping):
            raise ObservationSanitizationError()
        source_ref = output.get("source_ref")
        records = output.get("records")
        if (
            not isinstance(source_ref, str)
            or not re.fullmatch(r"read-[1-8]", source_ref)
            or not isinstance(records, (tuple, list))
            or len(records) > 50
        ):
            raise ObservationSanitizationError()

        if tool_name in {
            ToolName.ACADEMIC_LIST_COURSES.value,
            ToolName.ACADEMIC_GET_COURSE.value,
        }:
            allowed_fields = {"id", "title", "code", "description"}
        elif tool_name in {
            ToolName.ACADEMIC_LIST_ASSIGNMENTS.value,
            ToolName.ACADEMIC_GET_ASSIGNMENT.value,
        }:
            allowed_fields = {
                "id",
                "course_id",
                "course_title",
                "title",
                "description",
                "due_at",
            }
        elif tool_name == ToolName.ACADEMIC_LIST_DOCUMENTS.value:
            allowed_fields = {
                "id",
                "course_id",
                "title",
                "page_count",
                "index_readiness",
                "index_coverage",
            }
        else:
            raise ObservationSanitizationError()

        safe_records: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ObservationSanitizationError()
            safe_record: dict[str, object] = {}
            for key in sorted(allowed_fields.intersection(record)):
                value = record[key]
                if isinstance(value, str):
                    safe_record[key] = _sanitize_text(value, self._max_text_size, truncate=True)
                elif value is None or isinstance(value, (int, bool)):
                    safe_record[key] = value
                else:
                    raise ObservationSanitizationError()
            safe_records.append(safe_record)
        observation = ToolObservation(
            kind=tool_name,
            status="OK",
            fields=(
                ("records", tuple(safe_records)),
                ("source_ref", source_ref),
            ),
        )
        return observation, False

    def _rag_observation(self, result: ToolResult) -> ToolObservation:
        # Slice 3 owns the trusted Phase 4 response schema.  Reuse that
        # projection, then sanitize only the model-visible data representation.
        projected = _validated_rag_result_to_observation(result)
        if projected.status == "ERROR":
            return self._error_observation(result, ToolName.RAG_QUERY.value)
        safe_fields: list[tuple[str, object]] = []
        for key, value in projected.fields:
            if key == "citations":
                safe_fields.append((key, _sanitize_citations(value, self._max_text_size)))
            elif key in {"content_revision", "generation_id"}:
                if isinstance(value, int) and not isinstance(value, bool):
                    safe_fields.append((key, value))
            elif key in {"language", "status"} and isinstance(value, str):
                safe_fields.append((key, _safe_scalar_text(value, self._max_text_size)))
        if projected.text is None:
            raise ObservationSanitizationError()
        # Phase 4 answer text is untrusted data, but it remains legitimate
        # academic prose.  Preserve SQL-keyword prose while still removing
        # operational filesystem paths before model exposure.
        safe_text = _sanitize_text(projected.text, self._max_text_size, truncate=False)
        return ToolObservation(
            kind=ToolName.RAG_QUERY.value,
            status="OK",
            text=safe_text,
            fields=tuple(safe_fields),
        )

    def _generic_observation(
        self, result: ToolResult, tool_name: str
    ) -> tuple[ToolObservation, bool]:
        output = result.output
        if not isinstance(output, Mapping):
            # Arbitrary scalar/list output is not forwarded.  In particular,
            # do not expose a deterministic hash of private raw content as a
            # model-visible pseudo-identifier.
            return ToolObservation(kind=tool_name, status="OK"), False
        fields: list[tuple[str, object]] = []
        truncated = False
        for key in sorted(_GENERIC_FIELDS.get(tool_name, frozenset())):
            if key not in output:
                continue
            value = output[key]
            if key in {"task_id", "state", "status"}:
                if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
                    continue
                fields.append((key, value[:MAX_OBSERVATION_IDENTIFIER_SIZE]))
            elif key == "summary":
                if not isinstance(value, str):
                    continue
                safe, was_truncated = _bounded_sanitized_text(value, self._max_text_size)
                fields.append((key, safe))
                truncated = truncated or was_truncated
        return ToolObservation(kind=tool_name, status="OK", fields=tuple(fields)), truncated

    def _error_observation(self, result: ToolResult, tool_name: str) -> ToolObservation:
        if result.error is None:
            raise ObservationSanitizationError()
        message = _SAFE_ERROR_MESSAGES.get(result.error.code, "tool execution failed")
        return ToolObservation(
            kind=tool_name,
            status="ERROR",
            text=message,
            fields=(
                ("error_code", result.error.code.value),
                ("retryable", result.error.retryable),
            ),
        )

    def _fit_observation(
        self, observation: ToolObservation, *, truncated: bool
    ) -> tuple[ToolObservation, bool]:
        encoded = canonical_json(observation.to_dict())
        if len(encoded) <= self._max_size:
            return observation, truncated

        # Generic text has already been deterministically shortened.  A RAG
        # answer is an approved Phase 4 projection and is rejected rather than
        # silently changing its trusted answer text at this boundary.
        if observation.kind == ToolName.RAG_QUERY.value or observation.kind.startswith("academic."):
            raise ObservationSanitizationError()

        fields = dict(observation.fields)
        for key in ("summary",):
            value = fields.get(key)
            if isinstance(value, str):
                overhead = len(
                    canonical_json(
                        ToolObservation(
                            kind=observation.kind,
                            status=observation.status,
                            fields=tuple(
                                (field_key, field_value)
                                for field_key, field_value in fields.items()
                                if field_key != key
                            ),
                        ).to_dict()
                    )
                )
                allowed = max(1, self._max_size - overhead - 32)
                shortened = _truncate_text(value, allowed)
                fields[key] = shortened
                truncated = True
        fitted = ToolObservation(
            kind=observation.kind,
            status=observation.status,
            text=observation.text,
            fields=tuple(fields.items()),
        )
        if len(canonical_json(fitted.to_dict())) > self._max_size:
            raise ObservationSanitizationError()
        return fitted, truncated


def sanitize_tool_result(
    result: ToolResult,
    *,
    tool_name: ToolName | str,
    max_size: int = MAX_OBSERVATION_SIZE,
) -> ToolObservation:
    """Return one bounded model-visible observation or fail closed."""

    return ObservationSanitizer(max_size=max_size).sanitize(result, tool_name=tool_name)


def sanitize_tool_result_with_audit(
    result: ToolResult,
    *,
    tool_name: ToolName | str,
    max_size: int = MAX_OBSERVATION_SIZE,
) -> SanitizedObservation:
    """Return a bounded observation and non-sensitive audit metadata."""

    return ObservationSanitizer(max_size=max_size).sanitize_with_audit(result, tool_name=tool_name)


@dataclass(frozen=True, slots=True)
class Phase8ActivityObservation:
    """Safe typed projection for Phase 8 operational activity."""

    event_kind: Phase8ActivityKind
    resource_id: str
    status: Phase8ActivityStatus
    sequence_number: int
    timestamp_utc: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_kind": self.event_kind.value,
            "resource_id": self.resource_id,
            "status": self.status.value,
            "sequence_number": self.sequence_number,
            "timestamp_utc": self.timestamp_utc,
        }


def project_phase8_activity(event: Phase8ActivityEvent) -> Phase8ActivityObservation:
    """Project a trusted Phase 8 event without copying arbitrary payloads."""

    if not isinstance(event, Phase8ActivityEvent):
        raise TypeError("event must be a Phase8ActivityEvent")
    return Phase8ActivityObservation(
        event_kind=event.event_kind,
        resource_id=event.resource_id,
        status=event.status,
        sequence_number=event.sequence_number,
        timestamp_utc=event.timestamp_utc.isoformat(),
    )


def _normalize_tool_name(value: ToolName | str) -> str:
    if isinstance(value, ToolName):
        return value.value
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    raise ObservationSanitizationError()


def _safe_scalar_text(value: str, limit: int) -> str:
    safe = _sanitize_text(value, limit, truncate=False)
    if not safe or not _SAFE_IDENTIFIER.fullmatch(safe):
        raise ObservationSanitizationError()
    return safe


def _sanitize_citations(
    value: object,
    limit: int,
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (tuple, list)):
        raise ObservationSanitizationError()

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

    citations: list[dict[str, object]] = []

    for item in value:
        if not isinstance(item, Mapping):
            raise ObservationSanitizationError()

        fields = set(item)
        if fields not in {
            frozenset(page_fields),
            frozenset(structure_fields),
        }:
            raise ObservationSanitizationError()

        claim_id = item.get("claim_id")
        title = item.get("document_title")

        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ObservationSanitizationError()
        if not isinstance(title, str) or not title.strip():
            raise ObservationSanitizationError()

        common: dict[str, object] = {
            "claim_id": _sanitize_text(
                claim_id,
                limit,
                truncate=False,
            ),
            "document_version_id": _positive_int(item.get("document_version_id")),
            "document_title": _sanitize_text(
                title,
                limit,
                truncate=False,
            ),
        }

        if fields == page_fields:
            page_start = _positive_int(item.get("page_start"))
            page_end = _positive_int(item.get("page_end"))
            if page_end < page_start:
                raise ObservationSanitizationError()

            citations.append(
                {
                    **common,
                    "page_start": page_start,
                    "page_end": page_end,
                }
            )
            continue

        unit_start = _positive_int(item.get("unit_start"))
        unit_end = _positive_int(item.get("unit_end"))
        if unit_end < unit_start:
            raise ObservationSanitizationError()

        heading = item.get("heading")
        if heading is not None:
            if not isinstance(heading, str) or not heading.strip():
                raise ObservationSanitizationError()
            heading = _sanitize_text(
                heading,
                limit,
                truncate=False,
            )

        coordinate_values: list[int | None] = []
        for field in (
            "table_index",
            "row_index",
            "column_index",
        ):
            coordinate = item.get(field)
            if coordinate is None:
                coordinate_values.append(None)
                continue
            if isinstance(coordinate, bool) or not isinstance(coordinate, int) or coordinate < 0:
                raise ObservationSanitizationError()
            coordinate_values.append(coordinate)

        if any(coordinate is None for coordinate in coordinate_values) and not all(
            coordinate is None for coordinate in coordinate_values
        ):
            raise ObservationSanitizationError()

        citations.append(
            {
                **common,
                "unit_start": unit_start,
                "unit_end": unit_end,
                "heading": heading,
                "table_index": coordinate_values[0],
                "row_index": coordinate_values[1],
                "column_index": coordinate_values[2],
            }
        )

    return tuple(citations)


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ObservationSanitizationError()
    return value


def _bounded_sanitized_text(value: str, limit: int) -> tuple[str, bool]:
    safe = _sanitize_text(value, limit, truncate=False)
    if len(safe) <= limit:
        return safe, False
    return _truncate_text(safe, limit), True


def _sanitize_text(value: str, limit: int, *, truncate: bool, redact_paths: bool = True) -> str:
    if not isinstance(value, str):
        raise ObservationSanitizationError()
    # Remove private-key blocks before path ambiguity checks; encoded PEM
    # material may contain separators that are not meaningful filesystem
    # paths but would otherwise trigger the fail-closed guard.
    value = _PEM_PRIVATE_KEY.sub("[REDACTED_SECRET]", value)
    if redact_paths:
        _reject_ambiguous_spaced_path(value)
    sanitized = value
    sanitized = _TRACEBACK.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _EXCEPTION_REPR.sub("[REDACTED_ERROR]", sanitized)
    # Apply the inline form before the line-oriented fallback so a multiline
    # exception message is removed as one unit rather than leaving a
    # continuation line behind.
    sanitized = _INLINE_EXCEPTION.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _EXCEPTION_LINE.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _NESTED_EXCEPTION_OBJECT.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _STRUCTURED_DOUBLE_EXCEPTION.sub(_redact_structured_exception, sanitized)
    sanitized = _STRUCTURED_SINGLE_EXCEPTION.sub(_redact_structured_exception, sanitized)
    sanitized = _PEM_PRIVATE_KEY.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _URL_CREDENTIALS.sub(r"\1://[REDACTED_AUTH]@", sanitized)
    sanitized = _PROTOCOL_RELATIVE_CREDENTIALS.sub("//[REDACTED_AUTH]@", sanitized)
    sanitized = _AUTH_HEADER.sub("Authorization: [REDACTED_AUTH]", sanitized)
    sanitized = _BARE_AUTH_HEADER.sub("Authorization: [REDACTED_AUTH]", sanitized)
    sanitized = _COOKIE_HEADER.sub("Cookie: [REDACTED_AUTH]", sanitized)
    sanitized = _CLI_SECRET_FLAG.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _QUOTED_SECRET_ASSIGNMENT.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _SECRET_ASSIGNMENT.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _BEARER_TOKEN.sub("[REDACTED_AUTH]", sanitized)
    if redact_paths:
        sanitized = _WINDOWS_PATH.sub(_redact_path_match, sanitized)
        sanitized = _FILE_URI_PATH.sub(_redact_path_match, sanitized)
        sanitized = _UNC_DOTTED_SENSITIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _UNC_DOTTED_SHARE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _UNC_SLASH_PATH.sub(_redact_path_match, sanitized)
        sanitized = _UNC_ROOT.sub(_redact_path_match, sanitized)
        sanitized = _ENV_PATH.sub(_redact_path_match, sanitized)
        sanitized = _WINDOWS_SENSITIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _SPACED_POSIX_PATH.sub(_redact_path_match, sanitized)
        sanitized = _SPACED_DOTTED_RELATIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _SPACED_SENSITIVE_RELATIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _POSIX_PATH.sub(_redact_path_match, sanitized)
        sanitized = _POSIX_SENSITIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _RELATIVE_SENSITIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _DOT_RELATIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _RELATIVE_DOTTED_SENSITIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _RELATIVE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _RELATIVE_BARE_PATH.sub(_redact_path_match, sanitized)
        sanitized = _WINDOWS_DIRECTORY_PATH.sub(_redact_path_match, sanitized)
        sanitized = _POSIX_DIRECTORY_PATH.sub(_redact_path_match, sanitized)
        sanitized = _RELATIVE_DIRECTORY_PATH.sub(_redact_path_match, sanitized)
        sanitized = _DRIVE_ROOT.sub(_redact_path_match, sanitized)
        if (
            _WINDOWS_PATH_MARKER.search(sanitized)
            or _POSIX_PATH_MARKER.search(sanitized)
            or _RELATIVE_PATH_MARKER.search(sanitized)
        ):
            raise ObservationSanitizationError()
    sanitized = sanitized.strip()
    if not sanitized:
        raise ObservationSanitizationError()
    if truncate and len(sanitized) > limit:
        return _truncate_text(sanitized, limit)
    return sanitized


def _redact_path_match(match: re.Match[str]) -> str:
    """Replace a path while preserving sentence punctuation after it."""

    value = match.group(0)
    # Some defensive patterns intentionally over-approximate a path when
    # spaces make its boundary ambiguous.  If a file extension gives us a
    # safe boundary, retain the prose that follows it.  Handle compound
    # extensions (for example ``.tar.gz``) as one suffix.
    extension = re.search(
        r"\.[A-Za-z0-9]{1,16}(?:\.[A-Za-z0-9]{1,16})*"
        r"(?=[ \t,;:!?)}]|(?:\.(?![A-Za-z0-9]))|$)",
        value,
    )
    trailing_after_extension = ""
    if extension is not None:
        trailing_after_extension = value[extension.end() :]
        value = value[: extension.end()]
    trailing = ""
    while trailing_after_extension and trailing_after_extension[0] in _PATH_TRAILING_PUNCTUATION:
        trailing += trailing_after_extension[0]
        trailing_after_extension = trailing_after_extension[1:]
    trailing += trailing_after_extension
    while value and value[-1] in _PATH_TRAILING_PUNCTUATION:
        trailing = value[-1] + trailing
        value = value[:-1]
    return "[REDACTED_PATH]" + trailing


def _redact_structured_exception(match: re.Match[str]) -> str:
    """Redact a serialized exception field without exposing its message."""

    prefix = match.group(1)
    quote = match.group(0)[-1]
    value = match.group(0)[len(prefix) + 1 : -1]
    key = re.search(r"[\"']([^\"']+)[\"']\s*[:=]\s*$", prefix)
    if (
        key is not None and key.group(1).lower() in {"error", "exception", "trace", "detail"}
    ) or re.search(r"(?i)\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b", value):
        return f"{prefix}{quote}[REDACTED_ERROR]{quote}"
    return match.group(0)


def _reject_ambiguous_spaced_path(value: str) -> None:
    """Fail closed when whitespace makes a path boundary indeterminate."""

    if _AMBIGUOUS_SPACED_PATH.search(value):
        raise ObservationSanitizationError()
    relative = _AMBIGUOUS_SPACED_RELATIVE.search(value)
    if relative is not None and not re.search(
        r"\.[A-Za-z0-9]{1,16}(?=[ \t]|[.,;:!?)}]|$)", relative.group(0)
    ):
        raise ObservationSanitizationError()
    match = _SPACED_DRIVE_PATH.search(value)
    if (
        match is not None
        and not match.group(0).rstrip().endswith(("/", "\\"))
        and not re.search(r"\.[A-Za-z0-9]{1,16}(?=[ \t]|[.,;:!?)}]|$)", match.group(0))
    ):
        raise ObservationSanitizationError()
    for pattern in (
        _SPACED_POSIX_PATH,
        _SPACED_DOTTED_RELATIVE_PATH,
        _SPACED_SENSITIVE_RELATIVE_PATH,
    ):
        match = pattern.search(value)
        if (
            match is not None
            and not match.group(0).rstrip().endswith(("/", "\\"))
            and not re.search(r"\.[A-Za-z0-9]{1,16}(?=[ \t]|[.,;:!?)}]|$)", match.group(0))
        ):
            raise ObservationSanitizationError()


def _truncate_text(value: str, limit: int) -> str:
    marker = "…[TRUNCATED]"
    if limit <= len(marker):
        return marker[:limit]
    return value[: limit - len(marker)] + marker


__all__ = [
    "MAX_OBSERVATION_IDENTIFIER_SIZE",
    "MAX_OBSERVATION_SIZE",
    "MAX_OBSERVATION_TEXT_SIZE",
    "ObservationAudit",
    "ObservationSanitizationError",
    "ObservationSanitizer",
    "Phase8ActivityObservation",
    "SanitizedObservation",
    "project_phase8_activity",
    "sanitize_tool_result",
    "sanitize_tool_result_with_audit",
]
