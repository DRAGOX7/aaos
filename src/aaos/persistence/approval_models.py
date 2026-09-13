"""SQLAlchemy mappings for the Phase 6A durable approval ledger."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from aaos.approval.contracts import (
    ActionState,
    ApprovalState,
    EventKind,
    ExecutionOutcome,
    HumanDecisionReason,
    OperationState,
    TrustedInvalidationReason,
)
from aaos.persistence.academic_models import Base, UTCDateTime


def _sql_enum_values(enum_type: type[StrEnum]) -> str:
    return ",".join(f"'{member.value}'" for member in enum_type)


def _canonical_utc_text(column: str) -> str:
    """Normalize UTCDateTime text to a fixed-width, microsecond-safe value."""

    fraction = (
        f"CASE WHEN instr({column}, '.') = 0 THEN '000000' "
        f"ELSE substr(substr({column}, instr({column}, '.') + 1, "
        f"length({column}) - instr({column}, '.') - 1) || '000000', 1, 6) END"
    )
    return f"(substr({column}, 1, 19) || '.' || {fraction} || 'Z')"


_HASH_CHECK = "length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"
_APPROVAL_REASONS = (*HumanDecisionReason, *TrustedInvalidationReason)
_APPROVAL_REASON_SQL = ",".join(f"'{reason.value}'" for reason in _APPROVAL_REASONS)


class PreparedActionORM(Base):
    """Immutable action data whose complete digest is approved by a human."""

    __tablename__ = "prepared_actions"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_prepared_actions_operation_id"),
        UniqueConstraint("action_id", "operation_id", name="uq_prepared_actions_action_operation"),
        CheckConstraint(
            _HASH_CHECK.format(column="arguments_sha256"),
            name="ck_prepared_actions_arguments_sha256",
        ),
        CheckConstraint(
            _HASH_CHECK.format(column="tool_definition_fingerprint"),
            name="ck_prepared_actions_tool_definition_fingerprint",
        ),
        CheckConstraint(
            _HASH_CHECK.format(column="policy_fingerprint"),
            name="ck_prepared_actions_policy_fingerprint",
        ),
        CheckConstraint(
            _HASH_CHECK.format(column="prepared_action_sha256"),
            name="ck_prepared_actions_prepared_action_sha256",
        ),
        CheckConstraint(
            "length(canonical_arguments) > 0",
            name="ck_prepared_actions_canonical_arguments",
        ),
        CheckConstraint(
            "json_valid(target_identity) AND length(target_identity) <= 16384",
            name="ck_prepared_actions_target_identity",
        ),
        CheckConstraint(
            "json_valid(remote_precondition) AND length(remote_precondition) <= 16384",
            name="ck_prepared_actions_remote_precondition",
        ),
        CheckConstraint(
            f"{_canonical_utc_text('action_expires_at')} > {_canonical_utc_text('prepared_at')}",
            name="ck_prepared_actions_expiry",
        ),
        Index("ix_prepared_actions_actor", "actor_identity"),
        Index("ix_prepared_actions_action_type", "action_type"),
    )

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    target_identity: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_arguments: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_definition_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_precondition: Mapped[str] = mapped_column(Text, nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    action_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    prepared_action_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class ActionLifecycleORM(Base):
    """Mutable action lifecycle state, updated later through revision-checked CAS."""

    __tablename__ = "action_lifecycles"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({_sql_enum_values(ActionState)})",
            name="ck_action_lifecycles_state",
        ),
        CheckConstraint("revision >= 0", name="ck_action_lifecycles_revision"),
        CheckConstraint("updated_at >= created_at", name="ck_action_lifecycles_updated_at"),
        Index("ix_action_lifecycles_state", "state"),
    )

    action_id: Mapped[str] = mapped_column(
        ForeignKey("prepared_actions.action_id"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ApprovalRecordORM(Base):
    """A trusted approver decision bound to the immutable prepared action."""

    __tablename__ = "approval_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_approval_records_prepared_action",
        ),
        UniqueConstraint(
            "approval_id",
            "action_id",
            "operation_id",
            name="uq_approval_records_identity_binding",
        ),
        CheckConstraint(
            _HASH_CHECK.format(column="prepared_action_sha256"),
            name="ck_approval_records_prepared_action_sha256",
        ),
        CheckConstraint(
            _HASH_CHECK.format(column="approval_binding_sha256"),
            name="ck_approval_records_approval_binding_sha256",
        ),
        CheckConstraint(
            f"status IN ({_sql_enum_values(ApprovalState)})",
            name="ck_approval_records_status",
        ),
        CheckConstraint(
            f"reason_code IS NULL OR reason_code IN ({_APPROVAL_REASON_SQL})",
            name="ck_approval_records_reason_code",
        ),
        CheckConstraint(
            f"{_canonical_utc_text('approval_expires_at')} > {_canonical_utc_text('issued_at')}",
            name="ck_approval_records_expiry",
        ),
        Index("ix_approval_records_action_status", "action_id", "status"),
    )

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_context_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prepared_action_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    approval_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApprovalConsumptionORM(Base):
    """Authoritative one-shot approval-consumption fact."""

    __tablename__ = "approval_consumptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["approval_id", "action_id", "operation_id"],
            [
                "approval_records.approval_id",
                "approval_records.action_id",
                "approval_records.operation_id",
            ],
            name="fk_approval_consumptions_approval_binding",
        ),
        ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_approval_consumptions_prepared_action",
        ),
        UniqueConstraint("operation_id", name="uq_approval_consumptions_operation_id"),
        Index("ix_approval_consumptions_action", "action_id"),
    )

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claim_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OperationRecordORM(Base):
    """The durable lifecycle and bounded result of one execution attempt."""

    __tablename__ = "operation_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_operation_records_prepared_action",
        ),
        UniqueConstraint("action_id", name="uq_operation_records_action_id"),
        CheckConstraint(
            f"state IN ({_sql_enum_values(OperationState)})",
            name="ck_operation_records_state",
        ),
        CheckConstraint(
            f"outcome_kind IS NULL OR outcome_kind IN ({_sql_enum_values(ExecutionOutcome)})",
            name="ck_operation_records_outcome_kind",
        ),
        CheckConstraint("revision >= 0", name="ck_operation_records_revision"),
        CheckConstraint(
            "receipt_evidence IS NULL OR length(receipt_evidence) <= 2048",
            name="ck_operation_records_receipt_evidence",
        ),
        Index("ix_operation_records_state", "state"),
    )

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receipt_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reconciliation_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class ActionEventCounterORM(Base):
    """Atomic per-action event sequence allocator state."""

    __tablename__ = "action_event_counters"
    __table_args__ = (
        CheckConstraint("next_sequence >= 0", name="ck_action_event_counters_next_sequence"),
    )

    action_id: Mapped[str] = mapped_column(
        ForeignKey("prepared_actions.action_id"), primary_key=True
    )
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class ActionEventORM(Base):
    """Append-only, typed and bounded durable action audit event."""

    __tablename__ = "action_events"
    __table_args__ = (
        UniqueConstraint("action_id", "sequence", name="uq_action_events_action_sequence"),
        CheckConstraint("sequence >= 0", name="ck_action_events_sequence"),
        CheckConstraint(
            f"event_kind IN ({_sql_enum_values(EventKind)})",
            name="ck_action_events_event_kind",
        ),
        CheckConstraint(
            "json_valid(detail_payload) AND length(detail_payload) <= 16384",
            name="ck_action_events_detail_payload",
        ),
        Index("ix_action_events_action_sequence", "action_id", "sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("prepared_actions.action_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_payload: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(128), nullable=False)


__all__ = [
    "ActionEventCounterORM",
    "ActionEventORM",
    "ActionLifecycleORM",
    "ApprovalConsumptionORM",
    "ApprovalRecordORM",
    "OperationRecordORM",
    "PreparedActionORM",
]
