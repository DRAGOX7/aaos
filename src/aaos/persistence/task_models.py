"""SQLAlchemy mapping for the additive Phase 7E task ledger."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aaos.persistence.academic_models import Base, UTCDateTime
from aaos.tasks.contracts import TaskCreationState, TaskFailureCode, TaskKind


def _values(enum_type: type[object]) -> str:
    return ",".join(f"'{member.value}'" for member in enum_type)  # type: ignore[attr-defined]


class TaskRecordORM(Base):
    """Durable browser task identity and pre-action preparation state."""

    __tablename__ = "task_records"
    __table_args__ = (
        UniqueConstraint("actor_identity", "idempotency_key", name="uq_task_records_actor_key"),
        UniqueConstraint("action_id", name="uq_task_records_action_id"),
        CheckConstraint(
            f"task_kind IN ({_values(TaskKind)})",
            name="ck_task_records_task_kind",
        ),
        CheckConstraint(
            f"creation_state IN ({_values(TaskCreationState)})",
            name="ck_task_records_creation_state",
        ),
        CheckConstraint(
            f"failure_code IS NULL OR failure_code IN ({_values(TaskFailureCode)})",
            name="ck_task_records_failure_code",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_task_records_request_fingerprint",
        ),
        Index("ix_task_records_created_at", "created_at"),
        Index("ix_task_records_creation_state", "creation_state"),
    )

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    creation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_id: Mapped[str | None] = mapped_column(
        ForeignKey("prepared_actions.action_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[object] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[object] = mapped_column(UTCDateTime(), nullable=False)


__all__ = ["TaskRecordORM"]
