"""Static allowlisted tool registry and validated invocation boundary."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import TypeAlias

from .contracts import (
    AgentErrorCode,
    AgentProposal,
    PermissionClass,
    ToolArguments,
    ToolCall,
    ToolError,
    ToolName,
    ToolObservation,
    ToolResult,
    TrustedTaskScope,
)
from .policy import (
    RAG_ANSWER_LANGUAGES,
    PermissionPolicy,
    authorize_scope,
    parse_proposed_scope,
    rag_scope_model_contract,
)

MAX_TOOL_DESCRIPTION_CHARACTERS = 16_384
MAX_TOOL_TIMEOUT_SECONDS = 86_400
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class SideEffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOCAL_STATE_WRITE = "LOCAL_STATE_WRITE"
    TERMINAL = "TERMINAL"


class ToolValueType(StrEnum):
    STRING = "STRING"
    SAFE_IDENTIFIER = "SAFE_IDENTIFIER"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"


Handler: TypeAlias = Callable[["ValidatedToolArguments"], ToolResult]
ToolOutputValidator: TypeAlias = Callable[[ToolResult], ToolResult]
ToolObservationProjector: TypeAlias = Callable[[ToolResult, int], ToolObservation]

DEFAULT_TOOL_CONTRACT_VERSION = "phase5-tool-contract-v1"


def _safe_name(value: object, field: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    normalized = value.strip()
    if len(normalized) > limit or not _SAFE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field} must be a safe logical identifier")
    return normalized


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    normalized = value.strip()
    if len(normalized) > MAX_TOOL_DESCRIPTION_CHARACTERS:
        raise ValueError(f"{field} exceeds the maximum length")
    return normalized


def _freeze_json(value: object, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError(f"{path} object keys must be non-blank strings")
            frozen[key] = _freeze_json(nested, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(nested, f"{path}[]") for nested in value)
    raise TypeError(f"{path} contains a non-JSON value")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


@dataclass(frozen=True, slots=True)
class ToolField:
    name: str
    value_type: ToolValueType
    required: bool = True
    enum_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _safe_name(self.name, "field name", limit=64))
        if not isinstance(self.value_type, ToolValueType):
            raise TypeError("value_type must be a ToolValueType")
        if not isinstance(self.required, bool):
            raise TypeError("required must be a boolean")
        if not isinstance(self.enum_values, tuple):
            raise TypeError("enum_values must be a tuple")
        if self.enum_values and self.value_type not in {
            ToolValueType.STRING,
            ToolValueType.SAFE_IDENTIFIER,
        }:
            raise ValueError("enum_values require a string field")
        normalized = tuple(_bounded_text(value, "enum value") for value in self.enum_values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("enum_values must be unique")
        object.__setattr__(self, "enum_values", normalized)

    def to_dict(self) -> dict[str, object]:
        return {
            "enum_values": self.enum_values,
            "name": self.name,
            "required": self.required,
            "value_type": self.value_type.value,
        }


@dataclass(frozen=True, slots=True)
class ValidatedToolArguments:
    """Schema-owned immutable arguments that may reach a trusted handler."""

    schema_name: str
    values: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_name", _safe_name(self.schema_name, "schema_name", limit=64)
        )
        if not isinstance(self.values, tuple):
            raise TypeError("validated values must be a tuple")
        names: set[str] = set()
        normalized: list[tuple[str, object]] = []
        for item in self.values:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("validated argument entries must be key/value tuples")
            key = _safe_name(item[0], "argument key", limit=64)
            if key in names:
                raise ValueError("validated argument keys must be unique")
            names.add(key)
            normalized.append((key, _freeze_json(item[1], f"arguments.{key}")))
        object.__setattr__(self, "values", tuple(sorted(normalized)))

    @property
    def validated(self) -> bool:
        return True

    def to_mapping(self) -> dict[str, object]:
        return {key: _thaw_json(value) for key, value in self.values}

    def to_dict(self) -> dict[str, object]:
        return {"arguments": self.to_mapping(), "schema_name": self.schema_name}


@dataclass(frozen=True, slots=True)
class ToolInputSchema:
    fields: tuple[ToolField, ...] = ()
    schema_name: str = "tool_input"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "schema_name", _safe_name(self.schema_name, "schema_name", limit=64)
        )
        if not isinstance(self.fields, tuple):
            raise TypeError("fields must be a tuple")
        names: set[str] = set()
        for field in self.fields:
            if not isinstance(field, ToolField):
                raise TypeError("fields must contain ToolField values")
            if field.name in names:
                raise ValueError("schema field names must be unique")
            names.add(field.name)

    @classmethod
    def empty(cls, schema_name: str = "tool_input") -> ToolInputSchema:
        return cls((), schema_name)

    def validate(self, raw: ToolArguments) -> ValidatedToolArguments:
        if not isinstance(raw, ToolArguments) or raw.validated:
            raise ValueError("arguments must be raw ToolArguments")
        values = raw.to_mapping()
        field_map = {field.name: field for field in self.fields}
        unknown = set(values) - set(field_map)
        if unknown:
            raise ValueError("arguments contain unknown fields")
        missing = {
            field.name for field in self.fields if field.required and field.name not in values
        }
        if missing:
            raise ValueError("arguments are missing required fields")
        validated: list[tuple[str, object]] = []
        for field in self.fields:
            if field.name not in values:
                continue
            value = values[field.name]
            self._validate_value(field, value)
            validated.append((field.name, value))
        return ValidatedToolArguments(self.schema_name, tuple(validated))

    @staticmethod
    def _validate_value(field: ToolField, value: object) -> None:
        kind = field.value_type
        valid = (
            isinstance(value, str)
            if kind is ToolValueType.STRING
            else isinstance(value, str) and bool(_SAFE_IDENTIFIER.fullmatch(value))
            if kind is ToolValueType.SAFE_IDENTIFIER
            else isinstance(value, int) and not isinstance(value, bool)
            if kind is ToolValueType.INTEGER
            else isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            if kind is ToolValueType.NUMBER
            else isinstance(value, bool)
            if kind is ToolValueType.BOOLEAN
            else isinstance(value, Mapping)
            if kind is ToolValueType.OBJECT
            else isinstance(value, list)
            if kind is ToolValueType.ARRAY
            else False
        )
        if not valid:
            raise ValueError(f"argument {field.name} has an invalid type")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"argument {field.name} must not be blank")
        if field.enum_values and value not in field.enum_values:
            raise ValueError(f"argument {field.name} has an invalid enum value")

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": tuple(field.to_dict() for field in self.fields),
            "schema_name": self.schema_name,
        }


@dataclass(frozen=True, slots=True)
class ValidatedToolCall:
    name: ToolName
    arguments: ValidatedToolArguments
    call_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, ToolName):
            raise TypeError("name must be a ToolName")
        if not isinstance(self.arguments, ValidatedToolArguments):
            raise TypeError("arguments must be ValidatedToolArguments")
        if self.call_id is not None:
            object.__setattr__(self, "call_id", _safe_name(self.call_id, "call_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "arguments": self.arguments.to_mapping(),
            "name": self.name.value,
            **({"call_id": self.call_id} if self.call_id is not None else {}),
        }


def validate_generic_tool_result(result: ToolResult) -> ToolResult:
    """Validate the bounded JSON-object result used by non-RAG read tools."""

    if not isinstance(result, ToolResult):
        raise ValueError("handler returned an invalid ToolResult")
    if result.ok and not isinstance(result.output, Mapping):
        raise ValueError("successful tool output must be a JSON object")
    return result


def make_observation_projector(tool_name: ToolName) -> ToolObservationProjector:
    def project(result: ToolResult, max_size: int) -> ToolObservation:
        # Keep observations lazy so importing the registry never loads a
        # model, opens a store, starts a process, or mutates the filesystem.
        from .observations import (
            MAX_OBSERVATION_TEXT_SIZE,
            ObservationSanitizationError,
            ObservationSanitizer,
        )

        try:
            return ObservationSanitizer(
                max_size=max_size,
                max_text_size=min(MAX_OBSERVATION_TEXT_SIZE, max_size),
            ).sanitize(result, tool_name=tool_name)
        except ObservationSanitizationError as exc:
            raise ValueError("tool result could not be safely projected") from exc

    return project


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: ToolName
    description: str
    input_schema: ToolInputSchema
    required_permission: PermissionClass
    side_effect: SideEffectClass
    timeout_seconds: float
    handler: Handler | None = None
    observation_kind: str = "tool_result"
    result_schema: str = "tool_result"
    sanitization_policy: str = "safe_default"
    audit_version: str = "phase5-v1"
    output_validator: ToolOutputValidator | None = None
    observation_projector: ToolObservationProjector | None = None
    contract_version: str = DEFAULT_TOOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.name, ToolName):
            raise TypeError("name must be a ToolName")
        object.__setattr__(self, "description", _bounded_text(self.description, "description"))
        if not isinstance(self.input_schema, ToolInputSchema):
            raise TypeError("input_schema must be a ToolInputSchema")
        if not isinstance(self.required_permission, PermissionClass):
            raise TypeError("required_permission must be a PermissionClass")
        if not isinstance(self.side_effect, SideEffectClass):
            raise TypeError("side_effect must be a SideEffectClass")
        expected = {
            PermissionClass.READ_ONLY: SideEffectClass.READ_ONLY,
            PermissionClass.LOCAL_STATE_WRITE: SideEffectClass.LOCAL_STATE_WRITE,
            PermissionClass.TERMINAL: SideEffectClass.TERMINAL,
        }[self.required_permission]
        if self.side_effect is not expected:
            raise ValueError("side_effect must match required_permission")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if not isfinite(float(self.timeout_seconds)) or not (
            0 < float(self.timeout_seconds) <= MAX_TOOL_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if self.handler is not None and not callable(self.handler):
            raise TypeError("handler must be callable or None")
        if self.name is ToolName.WRITE_TASK_STATE and self.handler is not None:
            raise ValueError("write_task_state handler is disabled in Slice 2")
        object.__setattr__(
            self,
            "observation_kind",
            _safe_name(self.observation_kind, "observation_kind", limit=64),
        )
        object.__setattr__(
            self, "result_schema", _safe_name(self.result_schema, "result_schema", limit=64)
        )
        object.__setattr__(
            self,
            "sanitization_policy",
            _safe_name(self.sanitization_policy, "sanitization_policy", limit=64),
        )
        object.__setattr__(
            self, "audit_version", _safe_name(self.audit_version, "audit_version", limit=64)
        )
        object.__setattr__(
            self,
            "contract_version",
            _safe_name(self.contract_version, "contract_version", limit=64),
        )
        if self.output_validator is not None and not callable(self.output_validator):
            raise TypeError("output_validator must be callable or None")
        if self.observation_projector is not None and not callable(self.observation_projector):
            raise TypeError("observation_projector must be callable or None")

    @property
    def input_validator(self) -> Callable[[ToolArguments], ValidatedToolArguments]:
        """Return the immutable schema-owned validator for raw arguments."""

        return self.input_schema.validate

    def validate_output(self, result: ToolResult) -> ToolResult:
        """Validate trusted handler output before it can reach observation."""

        validator = self.output_validator
        if not callable(validator):
            raise ValueError("executable tool requires output_validator")
        validated = validator(result)
        if not isinstance(validated, ToolResult):
            raise ValueError("output_validator must return ToolResult")
        return validated

    def project_observation(self, result: ToolResult, *, max_size: int) -> ToolObservation:
        """Project validated output into the bounded C4 observation boundary."""

        projector = self.observation_projector
        if not callable(projector):
            raise ValueError("executable tool requires observation_projector")
        try:
            observation = projector(result, max_size)
        except Exception as exc:
            raise ValueError("observation projector failed safely") from exc
        if not isinstance(observation, ToolObservation):
            raise ValueError("observation_projector must return ToolObservation")
        return observation

    def to_metadata(self) -> dict[str, object]:
        return {
            "description": self.description,
            "input_schema": self.input_schema.to_dict(),
            "name": self.name.value,
            "observation_kind": self.observation_kind,
            "required_permission": self.required_permission.value,
            "result_schema": self.result_schema,
            "sanitization_policy": self.sanitization_policy,
            "side_effect": self.side_effect.value,
            "timeout_seconds": self.timeout_seconds,
            "audit_version": self.audit_version,
            "contract_version": self.contract_version,
            "output_contract": {
                "name": self.result_schema,
                "version": self.contract_version,
            },
        }

    def to_model_metadata(self) -> dict[str, object]:
        """Return the bounded capability description exposed to the model.

        Trusted registry metadata deliberately omits permission, timeout,
        handler, and side-effect authority.  Only an executable definition is
        selected by :meth:`ToolRegistry.model_visible_metadata`.
        """

        arguments: dict[str, object] = {}
        for field in self.input_schema.fields:
            value: dict[str, object] = {
                "required": field.required,
                "type": field.value_type.value.casefold(),
            }
            if field.enum_values:
                value["allowed_values"] = list(field.enum_values)
            if self.input_schema.schema_name == "rag_query" and field.name == "scope":
                value.update(rag_scope_model_contract())
            if self.input_schema.schema_name == "rag_query" and field.name == "answer_language":
                value["allowed_values"] = list(RAG_ANSWER_LANGUAGES)
            arguments[field.name] = value
        return {
            "arguments": arguments,
            "description": self.description,
            "name": self.name.value,
        }


class ToolInvocationError(ValueError):
    """Safe error raised before or at a trusted handler boundary."""

    def __init__(self, code: AgentErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


class ToolRegistry:
    """Application-owned immutable allowlist of trusted tool definitions."""

    def __init__(self, definitions: tuple[ToolDefinition, ...] | list[ToolDefinition] = ()) -> None:
        if not isinstance(definitions, (tuple, list)):
            raise TypeError("definitions must be a tuple or list")
        by_name: dict[ToolName, ToolDefinition] = {}
        ordered: list[ToolDefinition] = []
        for definition in definitions:
            if not isinstance(definition, ToolDefinition):
                raise ValueError("registry definitions must be ToolDefinition values")
            if definition.name in by_name:
                raise ValueError(
                    f"{AgentErrorCode.DUPLICATE_TOOL_REGISTRATION.value}: {definition.name.value}"
                )
            if definition.handler is not None:
                if not callable(definition.output_validator):
                    raise ValueError(
                        f"{AgentErrorCode.INVALID_TOOL_DEFINITION.value}: "
                        "executable tool requires output_validator"
                    )
                if not callable(definition.observation_projector):
                    raise ValueError(
                        f"{AgentErrorCode.INVALID_TOOL_DEFINITION.value}: "
                        "executable tool requires observation_projector"
                    )
            by_name[definition.name] = definition
            ordered.append(definition)
        self._definitions = MappingProxyType(by_name)
        self._ordered = tuple(ordered)

    @classmethod
    def default(cls) -> ToolRegistry:
        return cls(
            (
                ToolDefinition(
                    ToolName.RAG_QUERY,
                    "Query the frozen Phase 4 service with explicit scope",
                    ToolInputSchema(
                        (
                            ToolField("question", ToolValueType.STRING),
                            ToolField("scope", ToolValueType.OBJECT),
                            ToolField("answer_language", ToolValueType.STRING, required=False),
                        ),
                        "rag_query",
                    ),
                    PermissionClass.READ_ONLY,
                    SideEffectClass.READ_ONLY,
                    120,
                    output_validator=validate_generic_tool_result,
                    observation_projector=make_observation_projector(ToolName.RAG_QUERY),
                ),
                ToolDefinition(
                    ToolName.READ_TASK_STATE,
                    "Read narrow AAOS-owned task state",
                    ToolInputSchema(
                        (ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),), "read_task_state"
                    ),
                    PermissionClass.READ_ONLY,
                    SideEffectClass.READ_ONLY,
                    5,
                    output_validator=validate_generic_tool_result,
                    observation_projector=make_observation_projector(ToolName.READ_TASK_STATE),
                ),
                ToolDefinition(
                    ToolName.WRITE_TASK_STATE,
                    "Record a domain-specific AAOS task note",
                    ToolInputSchema(
                        (
                            ToolField("task_id", ToolValueType.SAFE_IDENTIFIER),
                            ToolField("note", ToolValueType.STRING),
                        ),
                        "write_task_state",
                    ),
                    PermissionClass.LOCAL_STATE_WRITE,
                    SideEffectClass.LOCAL_STATE_WRITE,
                    5,
                    output_validator=validate_generic_tool_result,
                    observation_projector=make_observation_projector(ToolName.WRITE_TASK_STATE),
                ),
                ToolDefinition(
                    ToolName.COMPLETE_TASK,
                    "Complete the current AAOS task",
                    ToolInputSchema(
                        (ToolField("result_text", ToolValueType.STRING),), "complete_task"
                    ),
                    PermissionClass.TERMINAL,
                    SideEffectClass.TERMINAL,
                    5,
                    output_validator=validate_generic_tool_result,
                    observation_projector=make_observation_projector(ToolName.COMPLETE_TASK),
                ),
            )
        )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._ordered

    @property
    def names(self) -> tuple[ToolName, ...]:
        return tuple(definition.name for definition in self._ordered)

    def model_visible_metadata(self) -> tuple[dict[str, object], ...]:
        """Return only currently executable trusted capabilities.

        Disabled conceptual tools remain registered for trusted rejection and
        audit tests but are never advertised as available model actions.
        """

        return tuple(
            definition.to_model_metadata()
            for definition in self._ordered
            if definition.handler is not None
        )

    def get(self, name: ToolName) -> ToolDefinition:
        if not isinstance(name, ToolName) or name not in self._definitions:
            raise ToolInvocationError(AgentErrorCode.UNKNOWN_TOOL, "tool is not registered")
        return self._definitions[name]

    def register(self, _: ToolDefinition) -> None:
        raise ValueError(f"{AgentErrorCode.DYNAMIC_TOOL_REGISTRATION.value}: registry is static")

    def prepare(
        self,
        proposal: AgentProposal,
        policy: PermissionPolicy,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ValidatedToolCall:
        if not isinstance(proposal, AgentProposal) or proposal.tool_call is None:
            raise ToolInvocationError(
                AgentErrorCode.INVALID_PROPOSAL, "a tool proposal is required"
            )
        if not isinstance(policy, PermissionPolicy):
            raise ToolInvocationError(
                AgentErrorCode.PERMISSION_DENIED, "permission policy is invalid"
            )
        call: ToolCall = proposal.tool_call
        try:
            definition = self.get(call.name)
            arguments = definition.input_validator(call.arguments)
        except ToolInvocationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ToolInvocationError(
                AgentErrorCode.INVALID_TOOL_ARGUMENTS, "arguments failed schema validation"
            ) from exc
        if definition.name is ToolName.RAG_QUERY and any(
            field.name == "scope" for field in definition.input_schema.fields
        ):
            try:
                proposed_scope = parse_proposed_scope(arguments.to_mapping().get("scope"))
            except (TypeError, ValueError) as exc:
                raise ToolInvocationError(
                    AgentErrorCode.INVALID_TOOL_ARGUMENTS, "RAG scope failed validation"
                ) from exc
            if not authorize_scope(proposed_scope, trusted_task_scope).allowed:
                raise ToolInvocationError(
                    AgentErrorCode.SCOPE_DENIED, "RAG scope was not authorized"
                )
        decision = policy.evaluate(definition)
        if not decision.allowed:
            raise ToolInvocationError(
                AgentErrorCode.PERMISSION_DENIED, "required permission was not granted"
            )
        if definition.handler is None:
            raise ToolInvocationError(
                AgentErrorCode.TOOL_NOT_EXECUTABLE, "tool handler is disabled"
            )
        return ValidatedToolCall(call.name, arguments, call.call_id)

    def execute(
        self,
        proposal: AgentProposal,
        policy: PermissionPolicy,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ToolResult:
        try:
            invocation = self.prepare(proposal, policy, trusted_task_scope=trusted_task_scope)
            definition = self.get(invocation.name)
            return self._execute_validated(definition, invocation, trusted_task_scope)
        except ToolInvocationError as exc:
            return ToolResult.failure(ToolError(exc.code, str(exc).split(": ", 1)[-1]))
        except Exception:
            return ToolResult.failure(
                ToolError(AgentErrorCode.HANDLER_EXECUTION_FAILED, "trusted tool execution failed")
            )

    async def execute_async(
        self,
        proposal: AgentProposal,
        policy: PermissionPolicy,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ToolResult:
        """Execute a validated tool without crossing event-loop ownership boundaries.

        Async-aware trusted handlers (currently the Phase 4 RAG adapter) run
        on the caller's event loop. Synchronous handlers are isolated in a
        worker thread, preserving the blocking-handler safety of ``execute``.
        """

        try:
            invocation = self.prepare(proposal, policy, trusted_task_scope=trusted_task_scope)
            definition = self.get(invocation.name)
            handler = definition.handler
            if handler is None:
                raise ToolInvocationError(
                    AgentErrorCode.TOOL_NOT_EXECUTABLE, "tool handler is disabled"
                )
            async_handler = getattr(handler, "handle_async", None)
            if definition.name is ToolName.RAG_QUERY and callable(async_handler):
                result = await async_handler(
                    invocation.arguments, trusted_task_scope=trusted_task_scope
                )
                if not isinstance(result, ToolResult):
                    raise ToolInvocationError(
                        AgentErrorCode.HANDLER_EXECUTION_FAILED,
                        "handler returned an invalid result",
                    )
                return definition.validate_output(result)
            return await asyncio.to_thread(
                self._execute_validated,
                definition,
                invocation,
                trusted_task_scope,
            )
        except ToolInvocationError as exc:
            return ToolResult.failure(ToolError(exc.code, str(exc).split(": ", 1)[-1]))
        except Exception:
            return ToolResult.failure(
                ToolError(AgentErrorCode.HANDLER_EXECUTION_FAILED, "trusted tool execution failed")
            )

    @staticmethod
    def _execute_validated(
        definition: ToolDefinition,
        invocation: ValidatedToolCall,
        trusted_task_scope: TrustedTaskScope | None,
    ) -> ToolResult:
        handler = definition.handler
        if handler is None:
            raise ToolInvocationError(
                AgentErrorCode.TOOL_NOT_EXECUTABLE, "tool handler is disabled"
            )
        if definition.name is ToolName.RAG_QUERY:
            scoped_handler = getattr(handler, "handle_with_scope", None)
            if callable(scoped_handler):
                result = scoped_handler(invocation.arguments, trusted_task_scope)
            else:
                result = handler(invocation.arguments)
        else:
            result = handler(invocation.arguments)
        if not isinstance(result, ToolResult):
            raise ToolInvocationError(
                AgentErrorCode.HANDLER_EXECUTION_FAILED, "handler returned an invalid result"
            )
        return definition.validate_output(result)

    def execute_payload(
        self,
        payload: Mapping[str, object],
        policy: PermissionPolicy,
        *,
        trusted_task_scope: TrustedTaskScope | None = None,
    ) -> ToolResult:
        if not isinstance(payload, Mapping):
            return ToolResult.failure(
                ToolError(AgentErrorCode.INVALID_PROPOSAL, "proposal must be an object")
            )
        raw_call = payload.get("tool_call")
        if isinstance(raw_call, Mapping):
            raw_name = raw_call.get("name")
            if isinstance(raw_name, str) and raw_name not in {name.value for name in ToolName}:
                return ToolResult.failure(
                    ToolError(AgentErrorCode.UNKNOWN_TOOL, "tool is not registered")
                )
        try:
            proposal = AgentProposal.from_mapping(payload)
        except Exception:
            return ToolResult.failure(
                ToolError(AgentErrorCode.INVALID_PROPOSAL, "proposal failed strict validation")
            )
        return self.execute(proposal, policy, trusted_task_scope=trusted_task_scope)


__all__ = [
    "DEFAULT_TOOL_CONTRACT_VERSION",
    "MAX_TOOL_TIMEOUT_SECONDS",
    "SideEffectClass",
    "ToolDefinition",
    "ToolField",
    "ToolInputSchema",
    "ToolInvocationError",
    "ToolObservationProjector",
    "ToolOutputValidator",
    "ToolRegistry",
    "ToolValueType",
    "make_observation_projector",
    "validate_generic_tool_result",
    "ValidatedToolArguments",
    "ValidatedToolCall",
]
