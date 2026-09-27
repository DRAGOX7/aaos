"""Persistence for bounded, complete Moodle synchronization history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from aaos.persistence.academic_models import Base, UTCDateTime


class MoodleSyncSnapshotORM(Base):
    __tablename__ = "moodle_sync_snapshots"
    __table_args__ = (
        CheckConstraint(
            "length(trim(source_system)) BETWEEN 1 AND 100",
            name="ck_moodle_sync_snapshots_source_system",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_moodle_sync_snapshots_status",
        ),
        CheckConstraint("snapshot_version = 1", name="ck_moodle_sync_snapshots_version"),
        CheckConstraint(
            "(status = 'RUNNING' AND finished_at IS NULL AND snapshot_state IS NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'COMPLETE' AND finished_at IS NOT NULL AND snapshot_state IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND finished_at IS NOT NULL AND snapshot_state IS NULL "
            "AND failure_code IN ('COLLECTION_INCOMPLETE', 'NORMALIZATION_FAILED', "
            "'SYNCHRONIZATION_FAILED'))",
            name="ck_moodle_sync_snapshots_outcome_shape",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_moodle_sync_snapshots_timestamp_order",
        ),
        Index(
            "uq_moodle_sync_snapshots_one_running",
            "source_system",
            unique=True,
            sqlite_where=text("status = 'RUNNING'"),
        ),
        Index(
            "ix_moodle_sync_snapshots_source_finished",
            "source_system",
            "finished_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    snapshot_state: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)


__all__ = ["MoodleSyncSnapshotORM"]
