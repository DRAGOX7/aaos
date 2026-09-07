"""Trusted, bounded projection of tool results into model-visible data.

The agent model receives observations only after this module has projected the
trusted :class:`ToolResult` into an allowlisted, JSON-compatible
``ToolObservation``.  No value in an observation grants permission, changes a
registry, or mutates task state; instruction-like document text is data.
"""

from __future__ import annotations

import hashlib
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
from .phase4_adapter import rag_result_to_observation

MAX_OBSERVATION_SIZE: Final[int] = MAX_AGENT_TEXT_CHARACTERS
"""Maximum canonical serialized size of one model-visible observation."""

MAX_OBSERVATION_TEXT_SIZE: Final[int] = 8_192
"""Maximum generic text field before deterministic truncation."""

MAX_OBSERVATION_IDENTIFIER_SIZE: Final[int] = 256
"""Maximum size of bounded identifiers retained in an observation."""

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\s\"'<>]+")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s\"'<>]+/)+[^\s\"'<>]*")
_AUTH_HEADER = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|x-api-key|x-auth-token)\s*:\s*"
    r"(?:bearer|basic)?\s*[^\s,;]+"
)
_COOKIE_HEADER = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*[^\s,;]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|oauth[_ -]?token|"
    r"session[_ -]?token|auth(?:orization)?[_ -]?(?:header|token|state)?|cookie|"
    r"token|password|secret|credential(?:s)?)\b\s*[:=]\s*[^\n]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SQL = re.compile(r"(?is)\b(?:select|insert|update|delete|create|alter|drop|pragma)\b.*?(?:;|$)")
_TRACEBACK = re.compile(r"(?is)traceback\s*\(most recent call last\):.*")
_EXCEPTION_REPR = re.compile(r"(?i)\b(?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\s*\([^\n]*\)")
_EXCEPTION_LINE = re.compile(r"(?im)^\s*[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\s*:[^\n]*")

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
    source_fingerprint: str
    input_size: int
    output_size: int
    truncated: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        for name in ("tool_name", "result_status", "sanitization_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a safe identifier")
        if (
            not isinstance(self.source_fingerprint, str)
            or len(self.source_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.source_fingerprint)
        ):
            raise ValueError("source_fingerprint must be a lowercase SHA-256 digest")
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
            "source_fingerprint": self.source_fingerprint,
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
            source_fingerprint = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
            if normalized_name == ToolName.RAG_QUERY.value:
                observation = self._rag_observation(result)
                truncated = False
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
                source_fingerprint=source_fingerprint,
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

    def _rag_observation(self, result: ToolResult) -> ToolObservation:
        # Slice 3 owns the trusted Phase 4 response schema.  Reuse that
        # projection, then sanitize only the model-visible data representation.
        projected = rag_result_to_observation(result)
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
            # Arbitrary scalar/list output is not forwarded; only its opaque
            # fingerprint is useful to a planner.
            return (
                ToolObservation(
                    kind=tool_name,
                    status="OK",
                    fields=(("output_fingerprint", _fingerprint(result)),),
                ),
                False,
            )
        fields: list[tuple[str, object]] = [
            ("output_fingerprint", _fingerprint(result)),
        ]
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
        if observation.kind == ToolName.RAG_QUERY.value:
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


def _normalize_tool_name(value: ToolName | str) -> str:
    if isinstance(value, ToolName):
        return value.value
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    raise ObservationSanitizationError()


def _fingerprint(result: ToolResult) -> str:
    try:
        return hashlib.sha256(canonical_json(result.to_dict()).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ObservationSanitizationError() from exc


def _safe_scalar_text(value: str, limit: int) -> str:
    safe = _sanitize_text(value, limit, truncate=False)
    if not safe or not _SAFE_IDENTIFIER.fullmatch(safe):
        raise ObservationSanitizationError()
    return safe


def _sanitize_citations(value: object, limit: int) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (tuple, list)):
        raise ObservationSanitizationError()
    citations: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ObservationSanitizationError()
        allowed = {"claim_id", "document_version_id", "document_title", "page_start", "page_end"}
        if set(item) != allowed:
            raise ObservationSanitizationError()
        claim_id = item.get("claim_id")
        title = item.get("document_title")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ObservationSanitizationError()
        if not isinstance(title, str) or not title.strip():
            raise ObservationSanitizationError()
        citations.append(
            {
                "claim_id": _sanitize_text(claim_id, limit, truncate=False),
                "document_title": _sanitize_text(title, limit, truncate=False),
                "document_version_id": _positive_int(item.get("document_version_id")),
                "page_end": _positive_int(item.get("page_end")),
                "page_start": _positive_int(item.get("page_start")),
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


def _sanitize_text(value: str, limit: int, *, truncate: bool) -> str:
    if not isinstance(value, str):
        raise ObservationSanitizationError()
    sanitized = value
    sanitized = _TRACEBACK.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _EXCEPTION_REPR.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _EXCEPTION_LINE.sub("[REDACTED_ERROR]", sanitized)
    sanitized = _SQL.sub("[REDACTED_QUERY]", sanitized)
    sanitized = _AUTH_HEADER.sub("Authorization: [REDACTED_AUTH]", sanitized)
    sanitized = _COOKIE_HEADER.sub("Cookie: [REDACTED_AUTH]", sanitized)
    sanitized = _SECRET_ASSIGNMENT.sub("[REDACTED_SECRET]", sanitized)
    sanitized = _BEARER_TOKEN.sub("[REDACTED_AUTH]", sanitized)
    sanitized = _WINDOWS_PATH.sub("[REDACTED_PATH]", sanitized)
    sanitized = _POSIX_PATH.sub("[REDACTED_PATH]", sanitized)
    sanitized = sanitized.strip()
    if not sanitized:
        raise ObservationSanitizationError()
    if truncate and len(sanitized) > limit:
        return _truncate_text(sanitized, limit)
    return sanitized


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
    "SanitizedObservation",
    "sanitize_tool_result",
    "sanitize_tool_result_with_audit",
]
