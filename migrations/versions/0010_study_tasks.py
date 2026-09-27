"""Add the separate local StudyTask domain and mutation idempotency ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_study_tasks"
down_revision = "0009_rag_structural_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="NORMAL"),
        sa.Column(
            "course_id",
            sa.Integer(),
            sa.ForeignKey("courses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 500",
            name="ck_study_tasks_title",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'COMPLETED')",
            name="ck_study_tasks_status",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH')",
            name="ck_study_tasks_priority",
        ),
        sa.CheckConstraint(
            "course_id IS NULL OR assignment_id IS NULL",
            name="ck_study_tasks_one_canonical_link",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_study_tasks_timestamp_order",
        ),
    )
    op.create_index(
        "ix_study_tasks_status_created",
        "study_tasks",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_study_tasks_course_created",
        "study_tasks",
        ["course_id", "created_at", "id"],
    )
    op.create_index("ix_study_tasks_assignment", "study_tasks", ["assignment_id"])
    op.create_table(
        "study_task_mutations",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "study_task_id",
            sa.Integer(),
            sa.ForeignKey("study_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint(
            "operation IN ('CREATE', 'UPDATE', 'COMPLETE')",
            name="ck_study_task_mutations_operation",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_study_task_mutations_fingerprint",
        ),
    )
    op.create_index(
        "ix_study_task_mutations_task",
        "study_task_mutations",
        ["study_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_study_task_mutations_task", table_name="study_task_mutations")
    op.drop_table("study_task_mutations")
    op.drop_index("ix_study_tasks_assignment", table_name="study_tasks")
    op.drop_index("ix_study_tasks_course_created", table_name="study_tasks")
    op.drop_index("ix_study_tasks_status_created", table_name="study_tasks")
    op.drop_table("study_tasks")
