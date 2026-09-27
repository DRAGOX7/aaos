"""SQLAlchemy mappings for the separate local StudyTask domain."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .academic_models import Base, UTCDateTime


class StudyTaskORM(Base):
    __tablename__ = "study_tasks"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 500", name="ck_study_tasks_title"),
        CheckConstraint(
            "status IN ('OPEN', 'COMPLETED')",
            name="ck_study_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH')",
            name="ck_study_tasks_priority",
        ),
        CheckConstraint(
            "course_id IS NULL OR assignment_id IS NULL",
            name="ck_study_tasks_one_canonical_link",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_study_tasks_timestamp_order",
        ),
        Index("ix_study_tasks_status_created", "status", "created_at", "id"),
        Index("ix_study_tasks_course_created", "course_id", "created_at", "id"),
        Index("ix_study_tasks_assignment", "assignment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", server_default="OPEN"
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NORMAL", server_default="NORMAL"
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"), nullable=True
    )
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class StudyTaskMutationORM(Base):
    __tablename__ = "study_task_mutations"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('CREATE', 'UPDATE', 'COMPLETE', 'APPLY_CHANGE')",
            name="ck_study_task_mutations_operation",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_study_task_mutations_fingerprint",
        ),
        CheckConstraint(
            "(operation = 'APPLY_CHANGE' AND "
            "source_change_id IS NOT NULL AND "
            "source_from_snapshot_id IS NOT NULL AND "
            "source_to_snapshot_id IS NOT NULL AND "
            "source_changed_field = 'due_at' AND "
            "length(trim(source_change_id)) BETWEEN 1 AND 255) OR "
            "(operation <> 'APPLY_CHANGE' AND "
            "source_change_id IS NULL AND "
            "source_from_snapshot_id IS NULL AND "
            "source_to_snapshot_id IS NULL AND "
            "source_changed_field IS NULL)",
            name="ck_study_task_mutations_change_source",
        ),
        Index("ix_study_task_mutations_task", "study_task_id"),
    )

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    study_task_id: Mapped[int] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_change_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_from_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("moodle_sync_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    source_to_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("moodle_sync_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    source_changed_field: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["StudyTaskMutationORM", "StudyTaskORM"]
