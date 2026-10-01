"""Cache verified document summaries per exact RAG and model identity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_rag_document_summaries"
down_revision = "0012_change_aware_study_task_mutations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_document_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "generation_id",
            sa.Integer(),
            sa.ForeignKey("rag_index_generations.id"),
            nullable=False,
        ),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("model_identifier", sa.String(128), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("sections_total", sa.Integer(), nullable=False),
        sa.Column("dropped_points", sa.Integer(), nullable=False),
        sa.Column("points_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint(
            "document_version_id",
            "generation_id",
            "content_revision",
            "policy_version",
            "model_identifier",
            "language",
            name="uq_rag_document_summaries_identity",
        ),
        sa.CheckConstraint("id > 0", name="ck_rag_document_summaries_id"),
        sa.CheckConstraint("content_revision >= 0", name="ck_rag_document_summaries_revision"),
        sa.CheckConstraint("language IN ('en','ar')", name="ck_rag_document_summaries_language"),
        sa.CheckConstraint("sections_total > 0", name="ck_rag_document_summaries_sections"),
        sa.CheckConstraint("dropped_points >= 0", name="ck_rag_document_summaries_dropped"),
    )


def downgrade() -> None:
    op.drop_table("rag_document_summaries")
