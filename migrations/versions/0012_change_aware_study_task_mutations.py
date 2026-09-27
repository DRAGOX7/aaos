"""Link explicit StudyTask deadline actions to trusted Moodle changes."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_change_aware_study_task_mutations"
down_revision = "0011_moodle_sync_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("study_task_mutations") as batch:
        batch.drop_constraint("ck_study_task_mutations_operation", type_="check")
        batch.add_column(sa.Column("source_change_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("source_from_snapshot_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_to_snapshot_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_changed_field", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_study_task_mutations_source_from_snapshot",
            "moodle_sync_snapshots",
            ["source_from_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_study_task_mutations_source_to_snapshot",
            "moodle_sync_snapshots",
            ["source_to_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_study_task_mutations_operation",
            "operation IN ('CREATE', 'UPDATE', 'COMPLETE', 'APPLY_CHANGE')",
        )
        batch.create_check_constraint(
            "ck_study_task_mutations_change_source",
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
        )


def downgrade() -> None:
    with op.batch_alter_table("study_task_mutations") as batch:
        batch.drop_constraint("ck_study_task_mutations_change_source", type_="check")
        batch.drop_constraint("ck_study_task_mutations_operation", type_="check")
        batch.drop_constraint("fk_study_task_mutations_source_from_snapshot", type_="foreignkey")
        batch.drop_constraint("fk_study_task_mutations_source_to_snapshot", type_="foreignkey")
        batch.drop_column("source_changed_field")
        batch.drop_column("source_to_snapshot_id")
        batch.drop_column("source_from_snapshot_id")
        batch.drop_column("source_change_id")
        batch.create_check_constraint(
            "ck_study_task_mutations_operation",
            "operation IN ('CREATE', 'UPDATE', 'COMPLETE')",
        )
