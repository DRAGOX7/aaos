"""Closed, bounded audit-event contracts for the Phase 6A approval ledger."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias, TypeVar, cast

from aaos.approval.contracts import (
    ActionId,
    ActorIdentity,
    ApprovalId,
    ClaimIdentity,
    EventId,
    EventKind,
    ExecutionOutcome,
    HumanDecisionReason,
    JSONValue,
    OperationId,
    Phase6ErrorCode,
    TrustedApproverContext,
    TrustedInvalidationReason,
)
from aaos.approval.state import TransitionSource

_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_EVENT_PAYLOAD = 16384


class InvalidEventDetails(ValueError):
    """Stable rejection for an untyped, mismatched, or malformed event payload."""

    def __init__(self) -> None:
        self.code = Phase6ErrorCode.INVALID_EVENT_DETAILS
        super().__init__(self.code.value)


def _require_type(value: object, expected: type[object]) -> None:
    if not isinstance(value, expected):
        raise InvalidEventDetails()


def _require_hash(value: object) -> None:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise InvalidEventDetails()


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidEventDetails()
    return value


def _require_utc(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise InvalidEventDetails()


def _utc_text(value: datetime) -> str:
    _require_utc(value)
    return value.isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidEventDetails()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidEventDetails() from error
    _require_utc(parsed)
    return parsed


@dataclass(frozen=True, slots=True)
class ActionPreparedDetails:
    operation_id: OperationId
    prepared_action_sha256: str

    def __post_init__(self) -> None:
        _require_type(self.operation_id, OperationId)
        _require_hash(self.prepared_action_sha256)


@dataclass(frozen=True, slots=True)
class ApprovalRequestedDetails:
    approval_id: ApprovalId
    approval_binding_sha256: str
    approval_expires_at: datetime

    def __post_init__(self) -> None:
        _require_type(self.approval_id, ApprovalId)
        _require_hash(self.approval_binding_sha256)
        _require_utc(self.approval_expires_at)


DecisionReason: TypeAlias = HumanDecisionReason | TrustedInvalidationReason


@dataclass(frozen=True, slots=True)
class ApprovalDecisionDetails:
    approval_id: ApprovalId
    reason: DecisionReason
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_type(self.approval_id, ApprovalId)
        if not isinstance(self.reason, (HumanDecisionReason, TrustedInvalidationReason)):
            raise InvalidEventDetails()
        _require_utc(self.decided_at)


@dataclass(frozen=True, slots=True)
class ClaimedDetails:
    approval_id: ApprovalId
    operation_id: OperationId
    claim_identity: ClaimIdentity

    def __post_init__(self) -> None:
        _require_type(self.approval_id, ApprovalId)
        _require_type(self.operation_id, OperationId)
        _require_type(self.claim_identity, ClaimIdentity)


@dataclass(frozen=True, slots=True)
class DispatchStartedDetails:
    operation_id: OperationId
    dispatch_started_at: datetime

    def __post_init__(self) -> None:
        _require_type(self.operation_id, OperationId)
        _require_utc(self.dispatch_started_at)


@dataclass(frozen=True, slots=True)
class OutcomeRecordedDetails:
    operation_id: OperationId
    outcome: ExecutionOutcome | None
    outcome_recorded_at: datetime
    reason: Phase6ErrorCode | None = None

    def __post_init__(self) -> None:
        _require_type(self.operation_id, OperationId)
        if self.outcome is not None and not isinstance(self.outcome, ExecutionOutcome):
            raise InvalidEventDetails()
        if self.reason is not None and not isinstance(self.reason, Phase6ErrorCode):
            raise InvalidEventDetails()
        _require_utc(self.outcome_recorded_at)


@dataclass(frozen=True, slots=True)
class ReconciliationDetails:
    operation_id: OperationId
    outcome: ExecutionOutcome
    reconciliation_at: datetime

    def __post_init__(self) -> None:
        _require_type(self.operation_id, OperationId)
        _require_type(self.outcome, ExecutionOutcome)
        _require_utc(self.reconciliation_at)


InvalidationReason: TypeAlias = TrustedInvalidationReason | Phase6ErrorCode


@dataclass(frozen=True, slots=True)
class InvalidatedDetails:
    reason: InvalidationReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, (TrustedInvalidationReason, Phase6ErrorCode)):
            raise InvalidEventDetails()


@dataclass(frozen=True, slots=True)
class LateResultIgnoredDetails:
    operation_id: OperationId
    reported_outcome: ExecutionOutcome | None
    reason: Phase6ErrorCode

    def __post_init__(self) -> None:
        _require_type(self.operation_id, OperationId)
        if self.reported_outcome is not None and not isinstance(
            self.reported_outcome, ExecutionOutcome
        ):
            raise InvalidEventDetails()
        _require_type(self.reason, Phase6ErrorCode)


ActionEventDetails: TypeAlias = (
    ActionPreparedDetails
    | ApprovalRequestedDetails
    | ApprovalDecisionDetails
    | ClaimedDetails
    | DispatchStartedDetails
    | OutcomeRecordedDetails
    | ReconciliationDetails
    | InvalidatedDetails
    | LateResultIgnoredDetails
)
EventActor: TypeAlias = ActorIdentity | ClaimIdentity | TrustedApproverContext

_DETAIL_TYPES: dict[EventKind, type[object]] = {
    EventKind.ACTION_PREPARED: ActionPreparedDetails,
    EventKind.APPROVAL_REQUESTED: ApprovalRequestedDetails,
    EventKind.APPROVAL_DECIDED: ApprovalDecisionDetails,
    EventKind.APPROVAL_CONSUMED: ClaimedDetails,
    EventKind.DISPATCH_STARTED: DispatchStartedDetails,
    EventKind.OUTCOME_RECORDED: OutcomeRecordedDetails,
    EventKind.RECONCILIATION_RECORDED: ReconciliationDetails,
    EventKind.ACTION_INVALIDATED: InvalidatedDetails,
    EventKind.LATE_RESULT_IGNORED: LateResultIgnoredDetails,
}


def _validate_actor(source: TransitionSource, actor: object) -> None:
    expected: type[object]
    if source is TransitionSource.TRUSTED_SYSTEM:
        expected = ActorIdentity
    elif source is TransitionSource.HUMAN_APPROVER:
        expected = TrustedApproverContext
    else:
        expected = ClaimIdentity
    _require_type(actor, expected)


def _details_operation_id(details: ActionEventDetails) -> OperationId | None:
    value = getattr(details, "operation_id", None)
    return value if isinstance(value, OperationId) else None


def _validate_event_fields(
    *,
    action_id: object,
    operation_id: object,
    event_kind: object,
    source: object,
    actor: object,
    occurred_at: object,
    details: object,
) -> None:
    _require_type(action_id, ActionId)
    if operation_id is not None:
        _require_type(operation_id, OperationId)
    _require_type(event_kind, EventKind)
    _require_type(source, TransitionSource)
    assert isinstance(event_kind, EventKind)
    assert isinstance(source, TransitionSource)
    _validate_actor(source, actor)
    _require_utc(occurred_at)
    if type(details) is not _DETAIL_TYPES[event_kind]:
        raise InvalidEventDetails()
    assert isinstance(
        details,
        (
            ActionPreparedDetails,
            ApprovalRequestedDetails,
            ApprovalDecisionDetails,
            ClaimedDetails,
            DispatchStartedDetails,
            OutcomeRecordedDetails,
            ReconciliationDetails,
            InvalidatedDetails,
            LateResultIgnoredDetails,
        ),
    )
    details_operation_id = _details_operation_id(details)
    if details_operation_id is not None and details_operation_id != operation_id:
        raise InvalidEventDetails()


@dataclass(frozen=True, slots=True)
class ActionEventDraft:
    action_id: ActionId
    operation_id: OperationId | None
    event_kind: EventKind
    source: TransitionSource
    actor: EventActor
    occurred_at: datetime
    details: ActionEventDetails

    def __post_init__(self) -> None:
        _validate_event_fields(
            action_id=self.action_id,
            operation_id=self.operation_id,
            event_kind=self.event_kind,
            source=self.source,
            actor=self.actor,
            occurred_at=self.occurred_at,
            details=self.details,
        )


@dataclass(frozen=True, slots=True)
class ActionEventRecord:
    event_id: EventId
    action_id: ActionId
    operation_id: OperationId | None
    sequence: int
    event_kind: EventKind
    source: TransitionSource
    actor: EventActor
    occurred_at: datetime
    details: ActionEventDetails

    def __post_init__(self) -> None:
        _require_type(self.event_id, EventId)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise InvalidEventDetails()
        _validate_event_fields(
            action_id=self.action_id,
            operation_id=self.operation_id,
            event_kind=self.event_kind,
            source=self.source,
            actor=self.actor,
            occurred_at=self.occurred_at,
            details=self.details,
        )


def _details_to_dict(details: ActionEventDetails) -> dict[str, object]:
    if isinstance(details, ActionPreparedDetails):
        return {
            "operation_id": details.operation_id.value,
            "prepared_action_sha256": details.prepared_action_sha256,
        }
    if isinstance(details, ApprovalRequestedDetails):
        return {
            "approval_id": details.approval_id.value,
            "approval_binding_sha256": details.approval_binding_sha256,
            "approval_expires_at": _utc_text(details.approval_expires_at),
        }
    if isinstance(details, ApprovalDecisionDetails):
        return {
            "approval_id": details.approval_id.value,
            "reason": details.reason.value,
            "reason_type": type(details.reason).__name__,
            "decided_at": _utc_text(details.decided_at),
        }
    if isinstance(details, ClaimedDetails):
        return {
            "approval_id": details.approval_id.value,
            "operation_id": details.operation_id.value,
            "claim_identity": details.claim_identity.value,
        }
    if isinstance(details, DispatchStartedDetails):
        return {
            "operation_id": details.operation_id.value,
            "dispatch_started_at": _utc_text(details.dispatch_started_at),
        }
    if isinstance(details, OutcomeRecordedDetails):
        return {
            "operation_id": details.operation_id.value,
            "outcome": details.outcome.value if details.outcome is not None else None,
            "outcome_recorded_at": _utc_text(details.outcome_recorded_at),
            "reason": details.reason.value if details.reason is not None else None,
        }
    if isinstance(details, ReconciliationDetails):
        return {
            "operation_id": details.operation_id.value,
            "outcome": details.outcome.value,
            "reconciliation_at": _utc_text(details.reconciliation_at),
        }
    if isinstance(details, InvalidatedDetails):
        return {"reason": details.reason.value, "reason_type": type(details.reason).__name__}
    if isinstance(details, LateResultIgnoredDetails):
        return {
            "operation_id": details.operation_id.value,
            "reported_outcome": (
                details.reported_outcome.value if details.reported_outcome is not None else None
            ),
            "reason": details.reason.value,
        }
    raise InvalidEventDetails()


def sanitize_event_details(details: ActionEventDetails) -> Mapping[str, JSONValue]:
    """Project one closed event-detail value into bounded JSON metadata.

    Event details are an application-owned union, not a generic payload
    channel.  Exact-type validation prevents subclasses or arbitrary objects
    from acquiring a durable audit representation at this boundary.
    """

    if type(details) not in _DETAIL_TYPES.values():
        raise InvalidEventDetails()
    try:
        projected = _details_to_dict(details)
        payload = json.dumps(projected, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload) > _MAX_EVENT_PAYLOAD:
            raise InvalidEventDetails()
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidEventDetails() from error
    return cast(Mapping[str, JSONValue], MappingProxyType(projected))


def _actor_to_dict(actor: EventActor) -> dict[str, str]:
    if isinstance(actor, TrustedApproverContext):
        return {
            "kind": "trusted_approver",
            "identity": actor.approver_identity.value,
            "context_id": actor.context_id,
        }
    if isinstance(actor, ClaimIdentity):
        return {"kind": "claim", "identity": actor.value}
    if isinstance(actor, ActorIdentity):
        return {"kind": "actor", "identity": actor.value}
    raise InvalidEventDetails()


def serialize_event_payload(draft: ActionEventDraft) -> str:
    """Serialize a validated draft to bounded canonical JSON for durable storage."""

    if not isinstance(draft, ActionEventDraft):
        raise InvalidEventDetails()
    payload = json.dumps(
        {
            "actor": _actor_to_dict(draft.actor),
            "details": _details_to_dict(draft.details),
            "operation_id": draft.operation_id.value if draft.operation_id is not None else None,
            "source": draft.source.value,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload) > _MAX_EVENT_PAYLOAD:
        raise InvalidEventDetails()
    return payload


def _require_fields(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidEventDetails()
    return value


_EnumT = TypeVar("_EnumT", bound=StrEnum)


def _enum(enum_type: type[_EnumT], value: object) -> _EnumT:
    try:
        return enum_type(_string(value))
    except ValueError as error:
        raise InvalidEventDetails() from error


def _optional_outcome(value: object) -> ExecutionOutcome | None:
    return None if value is None else _enum(ExecutionOutcome, value)


def _details_from_dict(event_kind: EventKind, value: object) -> ActionEventDetails:
    data: dict[str, object]
    try:
        if event_kind is EventKind.ACTION_PREPARED:
            data = _require_fields(value, {"operation_id", "prepared_action_sha256"})
            return ActionPreparedDetails(
                OperationId(_string(data["operation_id"])),
                _string(data["prepared_action_sha256"]),
            )
        if event_kind is EventKind.APPROVAL_REQUESTED:
            data = _require_fields(
                value, {"approval_id", "approval_binding_sha256", "approval_expires_at"}
            )
            return ApprovalRequestedDetails(
                ApprovalId(_string(data["approval_id"])),
                _string(data["approval_binding_sha256"]),
                _parse_utc(data["approval_expires_at"]),
            )
        if event_kind is EventKind.APPROVAL_DECIDED:
            data = _require_fields(value, {"approval_id", "reason", "reason_type", "decided_at"})
            reason_type = data["reason_type"]
            decision_reason = (
                _enum(HumanDecisionReason, data["reason"])
                if reason_type == HumanDecisionReason.__name__
                else _enum(TrustedInvalidationReason, data["reason"])
                if reason_type == TrustedInvalidationReason.__name__
                else None
            )
            if decision_reason is None:
                raise InvalidEventDetails()
            return ApprovalDecisionDetails(
                ApprovalId(_string(data["approval_id"])),
                decision_reason,
                _parse_utc(data["decided_at"]),
            )
        if event_kind is EventKind.APPROVAL_CONSUMED:
            data = _require_fields(value, {"approval_id", "operation_id", "claim_identity"})
            return ClaimedDetails(
                ApprovalId(_string(data["approval_id"])),
                OperationId(_string(data["operation_id"])),
                ClaimIdentity(_string(data["claim_identity"])),
            )
        if event_kind is EventKind.DISPATCH_STARTED:
            data = _require_fields(value, {"operation_id", "dispatch_started_at"})
            return DispatchStartedDetails(
                OperationId(_string(data["operation_id"])),
                _parse_utc(data["dispatch_started_at"]),
            )
        if event_kind is EventKind.OUTCOME_RECORDED:
            data = _require_fields(
                value, {"operation_id", "outcome", "outcome_recorded_at", "reason"}
            )
            reason_value = data["reason"]
            return OutcomeRecordedDetails(
                OperationId(_string(data["operation_id"])),
                _optional_outcome(data["outcome"]),
                _parse_utc(data["outcome_recorded_at"]),
                None if reason_value is None else _enum(Phase6ErrorCode, reason_value),
            )
        if event_kind is EventKind.RECONCILIATION_RECORDED:
            data = _require_fields(value, {"operation_id", "outcome", "reconciliation_at"})
            return ReconciliationDetails(
                OperationId(_string(data["operation_id"])),
                _enum(ExecutionOutcome, data["outcome"]),
                _parse_utc(data["reconciliation_at"]),
            )
        if event_kind is EventKind.ACTION_INVALIDATED:
            data = _require_fields(value, {"reason", "reason_type"})
            reason_type = data["reason_type"]
            invalidation_reason = (
                _enum(TrustedInvalidationReason, data["reason"])
                if reason_type == TrustedInvalidationReason.__name__
                else _enum(Phase6ErrorCode, data["reason"])
                if reason_type == Phase6ErrorCode.__name__
                else None
            )
            if invalidation_reason is None:
                raise InvalidEventDetails()
            return InvalidatedDetails(invalidation_reason)
        if event_kind is EventKind.LATE_RESULT_IGNORED:
            data = _require_fields(value, {"operation_id", "reported_outcome", "reason"})
            return LateResultIgnoredDetails(
                OperationId(_string(data["operation_id"])),
                _optional_outcome(data["reported_outcome"]),
                _enum(Phase6ErrorCode, data["reason"]),
            )
    except (TypeError, ValueError) as error:
        if isinstance(error, InvalidEventDetails):
            raise
        raise InvalidEventDetails() from error
    raise InvalidEventDetails()


def _actor_from_dict(value: object) -> EventActor:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise InvalidEventDetails()
    try:
        if value["kind"] == "actor":
            data = _require_fields(value, {"kind", "identity"})
            return ActorIdentity(_string(data["identity"]))
        if value["kind"] == "claim":
            data = _require_fields(value, {"kind", "identity"})
            return ClaimIdentity(_string(data["identity"]))
        if value["kind"] == "trusted_approver":
            data = _require_fields(value, {"kind", "identity", "context_id"})
            return TrustedApproverContext(
                ActorIdentity(_string(data["identity"])), _string(data["context_id"])
            )
    except (TypeError, ValueError) as error:
        if isinstance(error, InvalidEventDetails):
            raise
        raise InvalidEventDetails() from error
    raise InvalidEventDetails()


def deserialize_event_payload(
    payload: str,
    *,
    event_kind: EventKind,
) -> tuple[OperationId | None, TransitionSource, EventActor, ActionEventDetails]:
    """Reconstruct the closed event fields from a repository-owned payload."""

    if not isinstance(payload, str) or len(payload) > _MAX_EVENT_PAYLOAD:
        raise InvalidEventDetails()
    try:
        raw = json.loads(payload)
        data = _require_fields(raw, {"actor", "details", "operation_id", "source"})
        operation_value = data["operation_id"]
        operation_id = None if operation_value is None else OperationId(_string(operation_value))
        source = _enum(TransitionSource, data["source"])
        actor = _actor_from_dict(data["actor"])
        details = _details_from_dict(event_kind, data["details"])
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, InvalidEventDetails):
            raise
        raise InvalidEventDetails() from error
    _validate_event_fields(
        action_id=ActionId("payload-validation"),
        operation_id=operation_id,
        event_kind=event_kind,
        source=source,
        actor=actor,
        occurred_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        details=details,
    )
    return operation_id, source, actor, details


__all__ = [
    "ActionEventDetails",
    "ActionEventDraft",
    "ActionEventRecord",
    "ActionPreparedDetails",
    "ApprovalDecisionDetails",
    "ApprovalRequestedDetails",
    "ClaimedDetails",
    "DecisionReason",
    "DispatchStartedDetails",
    "EventActor",
    "InvalidEventDetails",
    "InvalidatedDetails",
    "InvalidationReason",
    "LateResultIgnoredDetails",
    "OutcomeRecordedDetails",
    "ReconciliationDetails",
    "deserialize_event_payload",
    "sanitize_event_details",
    "serialize_event_payload",
]
