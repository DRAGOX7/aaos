"""Pure, closed Phase 6A approval value contracts."""

from __future__ import annotations

import base64
import builtins
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite
from typing import Any, ClassVar

from aaos.agent.contracts import JSONValue, PermissionClass

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = object()


def _text(value: object, name: str, limit: int = 16384) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty bounded string")
    return value


def _id(value: object, name: str) -> str:
    value = _text(value, name, 128)
    if not _ID.fullmatch(value):
        raise ValueError(f"{name} must be a safe logical identifier")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise ValueError(f"{name} must be an aware UTC datetime")
    return value


def _time(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _fields(data: Mapping[str, object], names: set[str], name: str) -> None:
    if set(data) - names:
        raise ValueError(f"{name} contains unknown fields")
    if names - set(data):
        raise ValueError(f"{name} is missing required fields")


def _json(value: object, name: str, depth: int = 0) -> JSONValue:
    if depth > 32:
        raise ValueError(f"{name} exceeds maximum nesting depth")
    if (
        value is None
        or isinstance(value, bool)
        or (isinstance(value, int) and not isinstance(value, bool))
    ):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{name} contains a non-finite number")
        return value
    if isinstance(value, str):
        return _text(value, name)
    if isinstance(value, list):
        return [_json(v, name, depth + 1) for v in value]
    if isinstance(value, Mapping):
        return {
            _text(k, f"{name} key", 128): _json(v, f"{name}.{k}", depth + 1)
            for k, v in value.items()
        }
    raise TypeError(f"{name} contains a non-JSON value")


def _encode(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


@dataclass(frozen=True, slots=True)
class _Opaque:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _id(self.value, type(self).__name__))

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> "_Opaque":
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, Mapping):
            raise TypeError(f"{cls.__name__} must be a string")
        _fields(value, {"value"}, cls.__name__)
        return cls(value["value"])


class ActionId(_Opaque):
    pass


class OperationId(_Opaque):
    pass


class ApprovalId(_Opaque):
    pass


class EventId(_Opaque):
    pass


class ClaimIdentity(_Opaque):
    pass


class ActorIdentity(_Opaque):
    pass


@dataclass(frozen=True, slots=True)
class ActionType:
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _id(self.value, "action_type"))

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> "ActionType":
        if not isinstance(value, str):
            raise TypeError("action_type must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    provider: str
    account: str
    resource: str

    def __post_init__(self) -> None:
        for n in ("provider", "account", "resource"):
            object.__setattr__(self, n, _id(getattr(self, n), f"target.{n}"))

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "account": self.account, "resource": self.resource}

    @classmethod
    def from_dict(cls, value: object) -> "TargetIdentity":
        if not isinstance(value, Mapping):
            raise TypeError("target_identity must be an object")
        _fields(value, {"provider", "account", "resource"}, "target_identity")
        return cls(value["provider"], value["account"], value["resource"])


@dataclass(frozen=True, slots=True)
class TrustedApproverContext:
    approver_identity: ActorIdentity
    context_id: str

    def __init__(self, approver_identity: ActorIdentity | str, context_id: str) -> None:
        object.__setattr__(
            self,
            "approver_identity",
            approver_identity
            if isinstance(approver_identity, ActorIdentity)
            else ActorIdentity(approver_identity),
        )
        object.__setattr__(self, "context_id", _id(context_id, "approver context_id"))

    def to_dict(self) -> dict[str, str]:
        return {"approver_identity": self.approver_identity.value, "context_id": self.context_id}


@dataclass(frozen=True, slots=True)
class RemotePrecondition:
    provider: str
    resource: str
    version: str
    etag: str

    def __post_init__(self) -> None:
        for n in ("provider", "resource", "version", "etag"):
            object.__setattr__(self, n, _id(getattr(self, n), f"remote_precondition.{n}"))

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "resource": self.resource,
            "version": self.version,
            "etag": self.etag,
        }


@dataclass(frozen=True, slots=True, init=False)
class CanonicalArguments:
    bytes: builtins.bytes
    sha256: str

    def __init__(self, value: builtins.bytes, sha256: str, *, _token: object | None = None) -> None:
        if _token is not _TOKEN:
            raise ValueError("CanonicalArguments must be created by canonicalize_arguments")
        if (
            not isinstance(value, builtins.bytes)
            or not value
            or hashlib.sha256(value).hexdigest() != _hash(sha256, "arguments_sha256")
        ):
            raise ValueError("invalid canonical arguments")
        try:
            parsed = json.loads(value.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("canonical arguments must be UTF-8 JSON") from error
        if not isinstance(parsed, dict) or _encode(_json(parsed, "arguments")) != value:
            raise ValueError("arguments are not canonical JSON")
        object.__setattr__(self, "bytes", value)
        object.__setattr__(self, "sha256", sha256)

    def to_dict(self) -> dict[str, str]:
        return {"bytes_base64": base64.b64encode(self.bytes).decode(), "sha256": self.sha256}


def canonicalize_arguments(arguments: Mapping[str, JSONValue]) -> CanonicalArguments:
    if not isinstance(arguments, Mapping):
        raise TypeError("arguments must be a mapping")
    value = _json(dict(arguments), "arguments")
    if not isinstance(value, dict):
        raise TypeError("arguments must be a JSON object")
    raw = _encode(value)
    return CanonicalArguments(raw, hashlib.sha256(raw).hexdigest(), _token=_TOKEN)


class ActionState(StrEnum):
    PREPARED = "PREPARED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    RECONCILIATION_PENDING = "RECONCILIATION_PENDING"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class OperationState(StrEnum):
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    RECONCILIATION_PENDING = "RECONCILIATION_PENDING"


class ExecutionOutcome(StrEnum):
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class AdapterOutcomeKind(StrEnum):
    SUCCESS = "SUCCESS"
    AUTHORITATIVE_FAILURE = "AUTHORITATIVE_FAILURE"
    PRECONDITION_STALE = "PRECONDITION_STALE"
    UNKNOWN = "UNKNOWN"
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"


class EventKind(StrEnum):
    ACTION_PREPARED = "ACTION_PREPARED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    DISPATCH_STARTED = "DISPATCH_STARTED"
    OUTCOME_RECORDED = "OUTCOME_RECORDED"
    RECONCILIATION_RECORDED = "RECONCILIATION_RECORDED"
    ACTION_INVALIDATED = "ACTION_INVALIDATED"
    LATE_RESULT_IGNORED = "LATE_RESULT_IGNORED"


class HumanDecisionReason(StrEnum):
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    USER_REVOKED = "USER_REVOKED"


class TrustedInvalidationReason(StrEnum):
    ACTION_EXPIRED = "ACTION_EXPIRED"
    CAPABILITY_REVOKED = "CAPABILITY_REVOKED"
    SCHEMA_VERSION_DRIFT = "SCHEMA_VERSION_DRIFT"
    POLICY_VERSION_DRIFT = "POLICY_VERSION_DRIFT"
    BINDING_DRIFT = "BINDING_DRIFT"
    STORED_PRECONDITION_DRIFT = "STORED_PRECONDITION_DRIFT"


class Phase6ErrorCode(StrEnum):
    INVALID_PREPARED_ACTION = "INVALID_PREPARED_ACTION"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    APPROVAL_ALREADY_CONSUMED = "APPROVAL_ALREADY_CONSUMED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    ACTION_INVALIDATED = "ACTION_INVALIDATED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    PRECONDITION_STALE = "PRECONDITION_STALE"
    DISPATCH_ALREADY_RECORDED = "DISPATCH_ALREADY_RECORDED"
    INVALID_EVENT_DETAILS = "INVALID_EVENT_DETAILS"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ADAPTER_FAILURE = "ADAPTER_FAILURE"
    PERSISTENCE_CONFLICT = "PERSISTENCE_CONFLICT"


@dataclass(frozen=True, init=False)
class Phase6ActionDefinition:
    action_type: ActionType
    tool_name: str
    tool_schema_version: str
    tool_definition_fingerprint: str
    required_permission: PermissionClass
    policy_version: str
    policy_fingerprint: str
    timeout_seconds: int

    def __init__(
        self,
        action_type: ActionType,
        tool_name: str,
        tool_schema_version: str,
        tool_definition_fingerprint: str,
        required_permission: PermissionClass,
        policy_version: str,
        policy_fingerprint: str,
        timeout_seconds: int,
        argument_validator: Callable[
            [Mapping[str, JSONValue]], Mapping[str, JSONValue] | CanonicalArguments
        ],
    ) -> None:
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "tool_schema_version", tool_schema_version)
        object.__setattr__(self, "tool_definition_fingerprint", tool_definition_fingerprint)
        object.__setattr__(self, "required_permission", required_permission)
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "policy_fingerprint", policy_fingerprint)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        object.__setattr__(self, "_argument_validator", argument_validator)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, ActionType) or not isinstance(
            self.required_permission, PermissionClass
        ):
            raise TypeError("invalid action definition trust fields")
        for n in ("tool_name", "tool_schema_version", "policy_version"):
            object.__setattr__(self, n, _id(getattr(self, n), n))
        _hash(self.tool_definition_fingerprint, "tool_definition_fingerprint")
        _hash(self.policy_fingerprint, "policy_fingerprint")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 0 < self.timeout_seconds <= 86400
        ):
            raise ValueError("timeout_seconds must be positive and bounded")
        if not callable(getattr(self, "_argument_validator")):
            raise TypeError("argument_validator must be callable")

    def validate_arguments(self, arguments: Mapping[str, JSONValue]) -> CanonicalArguments:
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        value = getattr(self, "_argument_validator")(arguments)
        if isinstance(value, CanonicalArguments):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("argument_validator must return a mapping or CanonicalArguments")
        return canonicalize_arguments(value)


@dataclass(frozen=True, slots=True)
class PrepareActionRequest:
    """Trusted application inputs for preparing one approval-bound action."""

    actor_identity: ActorIdentity
    target_identity: TargetIdentity
    arguments: Mapping[str, JSONValue]
    remote_precondition: RemotePrecondition
    action_expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.actor_identity, ActorIdentity):
            raise TypeError("actor_identity must be an ActorIdentity")
        if not isinstance(self.target_identity, TargetIdentity):
            raise TypeError("target_identity must be a TargetIdentity")
        if not isinstance(self.remote_precondition, RemotePrecondition):
            raise TypeError("remote_precondition must be a RemotePrecondition")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        canonicalize_arguments(self.arguments)
        _utc(self.action_expires_at, "action_expires_at")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """A closed human decision command from an authenticated approver boundary."""

    approval_id: ApprovalId
    approver: TrustedApproverContext
    reason: HumanDecisionReason
    decided_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.approval_id, ApprovalId):
            raise TypeError("approval_id must be an ApprovalId")
        if not isinstance(self.approver, TrustedApproverContext):
            raise TypeError("approver must be a TrustedApproverContext")
        if not isinstance(self.reason, HumanDecisionReason):
            raise TypeError("reason must be a HumanDecisionReason")
        _utc(self.decided_at, "decided_at")


@dataclass(frozen=True, slots=True)
class PreparedActionBinding:
    action_id: ActionId
    operation_id: OperationId
    actor_identity: ActorIdentity
    target_identity: TargetIdentity
    action_type: ActionType
    canonical_arguments: bytes
    arguments_sha256: str
    tool_name: str
    tool_schema_version: str
    tool_definition_fingerprint: str
    policy_version: str
    policy_fingerprint: str
    remote_precondition: RemotePrecondition
    prepared_at: datetime
    action_expires_at: datetime

    def __post_init__(self) -> None:
        _validate_prepared(self)


PREPARED_ACTION_HASH_FIELDS = (
    "action_id",
    "operation_id",
    "actor_identity",
    "target_identity",
    "action_type",
    "canonical_arguments",
    "arguments_sha256",
    "tool_name",
    "tool_schema_version",
    "tool_definition_fingerprint",
    "policy_version",
    "policy_fingerprint",
    "remote_precondition",
    "action_expires_at",
)


def _validate_prepared(v: Any) -> None:
    for n, t in (
        ("action_id", ActionId),
        ("operation_id", OperationId),
        ("actor_identity", ActorIdentity),
        ("target_identity", TargetIdentity),
        ("action_type", ActionType),
        ("remote_precondition", RemotePrecondition),
    ):
        if not isinstance(getattr(v, n), t):
            raise TypeError(f"{n} has invalid type")
    CanonicalArguments(v.canonical_arguments, v.arguments_sha256, _token=_TOKEN)
    for n in ("tool_name", "tool_schema_version", "policy_version"):
        _id(getattr(v, n), n)
    _hash(v.tool_definition_fingerprint, "tool_definition_fingerprint")
    _hash(v.policy_fingerprint, "policy_fingerprint")
    if _utc(v.action_expires_at, "action_expires_at") <= _utc(v.prepared_at, "prepared_at"):
        raise ValueError("action_expires_at must follow prepared_at")


def _prepared_dict(v: Any) -> dict[str, object]:
    return {
        "action_id": v.action_id.value,
        "operation_id": v.operation_id.value,
        "actor_identity": v.actor_identity.value,
        "target_identity": v.target_identity.to_dict(),
        "action_type": v.action_type.value,
        "canonical_arguments": base64.b64encode(v.canonical_arguments).decode(),
        "arguments_sha256": v.arguments_sha256,
        "tool_name": v.tool_name,
        "tool_schema_version": v.tool_schema_version,
        "tool_definition_fingerprint": v.tool_definition_fingerprint,
        "policy_version": v.policy_version,
        "policy_fingerprint": v.policy_fingerprint,
        "remote_precondition": v.remote_precondition.to_dict(),
        "prepared_at": _time(v.prepared_at),
        "action_expires_at": _time(v.action_expires_at),
    }


def compute_prepared_action_sha256(binding: PreparedActionBinding) -> str:
    if not isinstance(binding, PreparedActionBinding):
        raise TypeError("binding must be a PreparedActionBinding")
    d = _prepared_dict(binding)
    return hashlib.sha256(
        _encode([{"field": n, "value": d[n]} for n in PREPARED_ACTION_HASH_FIELDS])
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedAction(PreparedActionBinding):
    prepared_action_sha256: str

    def __post_init__(self) -> None:
        _validate_prepared(self)
        binding = PreparedActionBinding(
            **{n: getattr(self, n) for n in PreparedActionBinding.__dataclass_fields__}
        )
        if self.prepared_action_sha256 != compute_prepared_action_sha256(binding):
            raise ValueError("prepared_action_sha256 does not match immutable binding")

    def to_dict(self) -> dict[str, object]:
        d = _prepared_dict(self)
        d["prepared_action_sha256"] = self.prepared_action_sha256
        return d


def build_prepared_action(binding: PreparedActionBinding) -> PreparedAction:
    if not isinstance(binding, PreparedActionBinding):
        raise TypeError("binding must be a PreparedActionBinding")
    return PreparedAction(
        **{n: getattr(binding, n) for n in PreparedActionBinding.__dataclass_fields__},
        prepared_action_sha256=compute_prepared_action_sha256(binding),
    )


def compute_approval_binding_sha256(
    prepared_action_sha256: str, approver: TrustedApproverContext, approval_expires_at: datetime
) -> str:
    if not isinstance(approver, TrustedApproverContext):
        raise TypeError("approver must be a TrustedApproverContext")
    return hashlib.sha256(
        _encode(
            [
                {
                    "field": "prepared_action_sha256",
                    "value": _hash(prepared_action_sha256, "prepared_action_sha256"),
                },
                {"field": "approver", "value": approver.to_dict()},
                {
                    "field": "approval_expires_at",
                    "value": _time(_utc(approval_expires_at, "approval_expires_at")),
                },
            ]
        )
    ).hexdigest()


@dataclass(slots=True)
class ApprovalRecord:
    approval_id: ApprovalId
    action_id: ActionId
    operation_id: OperationId
    approver: TrustedApproverContext
    prepared_action_sha256: str
    approval_binding_sha256: str
    issued_at: datetime
    approval_expires_at: datetime
    action_expires_at: datetime
    status: ApprovalState
    decided_at: datetime | None = None
    reason: HumanDecisionReason | TrustedInvalidationReason | None = None

    _MUTABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"status", "decided_at", "reason"})

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name) and name not in self._MUTABLE_FIELDS:
            raise AttributeError(f"{name} is immutable after approval issue")
        if name == "status" and not isinstance(value, ApprovalState):
            raise TypeError("status must be an ApprovalState")
        if name == "decided_at" and value is not None:
            _utc(value, "decided_at")
        if (
            name == "reason"
            and value is not None
            and not isinstance(value, (HumanDecisionReason, TrustedInvalidationReason))
        ):
            raise TypeError("reason must be closed")
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.approval_id, ApprovalId)
            or not isinstance(self.action_id, ActionId)
            or not isinstance(self.operation_id, OperationId)
            or not isinstance(self.approver, TrustedApproverContext)
            or not isinstance(self.status, ApprovalState)
        ):
            raise TypeError("invalid approval fields")
        if self.approval_binding_sha256 != compute_approval_binding_sha256(
            self.prepared_action_sha256, self.approver, self.approval_expires_at
        ):
            raise ValueError("approval_binding_sha256 does not match immutable binding")
        if _utc(self.approval_expires_at, "approval_expires_at") <= _utc(
            self.issued_at, "issued_at"
        ) or self.approval_expires_at > _utc(self.action_expires_at, "action_expires_at"):
            raise ValueError(
                "approval_expires_at must be after issue and no later than action_expires_at"
            )
        if self.reason is not None and not isinstance(
            self.reason, (HumanDecisionReason, TrustedInvalidationReason)
        ):
            raise TypeError("reason must be closed")


@dataclass(frozen=True, slots=True)
class ApprovalConsumption:
    approval_id: ApprovalId
    action_id: ActionId
    operation_id: OperationId
    claim_identity: ClaimIdentity
    consumed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.approval_id, ApprovalId):
            raise TypeError("approval_id must be an ApprovalId")
        if not isinstance(self.action_id, ActionId):
            raise TypeError("action_id must be an ActionId")
        if not isinstance(self.operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId")
        if not isinstance(self.claim_identity, ClaimIdentity):
            raise TypeError("claim_identity must be a ClaimIdentity")
        _utc(self.consumed_at, "consumed_at")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: OperationId
    action_id: ActionId
    state: OperationState
    outcome: ExecutionOutcome | None = None
    receipt_evidence: str | None = None
    dispatch_started_at: datetime | None = None
    outcome_recorded_at: datetime | None = None
    reconciliation_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, OperationId) or not isinstance(
            self.action_id, ActionId
        ):
            raise TypeError("operation record identities must be trusted IDs")
        if not isinstance(self.state, OperationState):
            raise TypeError("state must be an OperationState")
        if self.outcome is not None and not isinstance(self.outcome, ExecutionOutcome):
            raise TypeError("outcome must be an ExecutionOutcome")
        if self.receipt_evidence is not None:
            object.__setattr__(
                self, "receipt_evidence", _text(self.receipt_evidence, "receipt_evidence", 2048)
            )
        for name in ("dispatch_started_at", "outcome_recorded_at", "reconciliation_at"):
            value = getattr(self, name)
            if value is not None:
                _utc(value, name)


@dataclass(frozen=True, slots=True)
class ExecutableAction:
    target_identity: TargetIdentity
    action_type: ActionType
    canonical_arguments: builtins.bytes
    operation_id: OperationId
    remote_precondition: RemotePrecondition

    def __post_init__(self) -> None:
        if not isinstance(self.target_identity, TargetIdentity):
            raise TypeError("target_identity must be a TargetIdentity")
        if not isinstance(self.action_type, ActionType):
            raise TypeError("action_type must be an ActionType")
        if not isinstance(self.operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId")
        if not isinstance(self.remote_precondition, RemotePrecondition):
            raise TypeError("remote_precondition must be a RemotePrecondition")
        if not isinstance(self.canonical_arguments, builtins.bytes):
            raise TypeError("canonical_arguments must be bytes")
        try:
            parsed = json.loads(self.canonical_arguments.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("canonical_arguments must be UTF-8 JSON") from error
        if (
            not isinstance(parsed, dict)
            or _encode(_json(parsed, "arguments")) != self.canonical_arguments
        ):
            raise ValueError("canonical_arguments must be strict canonical JSON")

    def to_dict(self) -> dict[str, object]:
        return {
            "target_identity": self.target_identity.to_dict(),
            "action_type": self.action_type.value,
            "canonical_arguments": base64.b64encode(self.canonical_arguments).decode(),
            "operation_id": self.operation_id.value,
            "remote_precondition": self.remote_precondition.to_dict(),
        }


def project_executable_action(prepared: PreparedAction) -> ExecutableAction:
    if not isinstance(prepared, PreparedAction):
        raise TypeError("prepared must be PreparedAction")
    return ExecutableAction(
        prepared.target_identity,
        prepared.action_type,
        prepared.canonical_arguments,
        prepared.operation_id,
        prepared.remote_precondition,
    )


@dataclass(frozen=True, slots=True)
class ActionRecord:
    prepared: PreparedAction
    action_state: ActionState
    approval: ApprovalRecord | None = None
    consumption: ApprovalConsumption | None = None
    operation: OperationRecord | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedAction):
            raise TypeError("prepared must be a PreparedAction")
        if not isinstance(self.action_state, ActionState):
            raise TypeError("action_state must be an ActionState")
        for name, expected in (
            ("approval", ApprovalRecord),
            ("consumption", ApprovalConsumption),
            ("operation", OperationRecord),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{name} has an invalid record type")


@dataclass(frozen=True, slots=True)
class AdapterExecutionOutcome:
    kind: AdapterOutcomeKind
    evidence: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AdapterOutcomeKind):
            raise TypeError("kind must be AdapterOutcomeKind")
        object.__setattr__(self, "evidence", _text(self.evidence, "evidence", 2048))


@dataclass(frozen=True, slots=True)
class AdapterReconciliationOutcome(AdapterExecutionOutcome):
    pass


__all__ = [name for name in globals() if not name.startswith("_")]
