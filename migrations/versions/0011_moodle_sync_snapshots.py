"""Persist bounded complete Moodle snapshots for deterministic change queries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_moodle_sync_snapshots"
down_revision = "0010_study_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moodle_sync_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_state", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(40), nullable=True),
        sa.CheckConstraint(
            "length(trim(source_system)) BETWEEN 1 AND 100",
            name="ck_moodle_sync_snapshots_source_system",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE', 'FAILED')",
            name="ck_moodle_sync_snapshots_status",
        ),
        sa.CheckConstraint("snapshot_version = 1", name="ck_moodle_sync_snapshots_version"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND finished_at IS NULL AND snapshot_state IS NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'COMPLETE' AND finished_at IS NOT NULL AND snapshot_state IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND finished_at IS NOT NULL AND snapshot_state IS NULL "
            "AND failure_code IN ('COLLECTION_INCOMPLETE', 'NORMALIZATION_FAILED', "
            "'SYNCHRONIZATION_FAILED'))",
            name="ck_moodle_sync_snapshots_outcome_shape",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_moodle_sync_snapshots_timestamp_order",
        ),
    )
    op.create_index(
        "uq_moodle_sync_snapshots_one_running",
        "moodle_sync_snapshots",
        ["source_system"],
        unique=True,
        sqlite_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "ix_moodle_sync_snapshots_source_finished",
        "moodle_sync_snapshots",
        ["source_system", "finished_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_moodle_sync_snapshots_source_finished",
        table_name="moodle_sync_snapshots",
    )
    op.drop_index(
        "uq_moodle_sync_snapshots_one_running",
        table_name="moodle_sync_snapshots",
    )
    op.drop_table("moodle_sync_snapshots")
