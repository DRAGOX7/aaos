"""Persistence for Phase 10 notification state, events and deliveries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aaos.persistence.academic_models import Base


class NotificationStateORM(Base):
    __tablename__ = "notification_state"

    source_system: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled_at: Mapped[str | None] = mapped_column(String(40))
    baseline_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    last_processed_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_alert_after_snapshot_id: Mapped[int | None] = mapped_column(Integer)


class NotificationEventORM(Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    course_source_id: Mapped[str | None] = mapped_column(String(255))
    entity_source_id: Mapped[str | None] = mapped_column(String(255))
    from_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    to_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class NotificationDeliveryORM(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", name="uq_notification_deliveries_event_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[str | None] = mapped_column(String(40))
    last_error_code: Mapped[str | None] = mapped_column(String(32))
    sent_at: Mapped[str | None] = mapped_column(String(40))
