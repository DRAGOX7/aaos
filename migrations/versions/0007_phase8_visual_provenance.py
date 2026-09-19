"""Add immutable Phase 8 visual provenance tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_phase8_visual_provenance"
down_revision = "0006_phase7e_task_records"
branch_labels = None
depends_on = None


def _utc_datetime() -> sa.TypeDecorator[object]:
    class UTCText(sa.TypeDecorator[object]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def upgrade() -> None:
    op.create_table(
        "visual_analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_key", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_content_sha256", sa.String(64), nullable=False),
        sa.Column("render_sha256", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("model_revision", sa.String(128), nullable=False),
        sa.Column("prompt_revision", sa.String(128), nullable=False),
        sa.Column("selector_revision", sa.String(128), nullable=False),
        sa.Column("renderer_revision", sa.String(128), nullable=False),
        sa.Column("analysis_schema_revision", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("analysis_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint(
            "analysis_key", "attempt_number", name="uq_visual_analysis_runs_key_attempt"
        ),
        sa.CheckConstraint("id > 0", name="ck_visual_analysis_runs_id"),
        sa.CheckConstraint("attempt_number > 0", name="ck_visual_analysis_runs_attempt_number"),
        sa.CheckConstraint(
            "document_version_id > 0", name="ck_visual_analysis_runs_document_version_id"
        ),
        sa.CheckConstraint("page_number > 0", name="ck_visual_analysis_runs_page_number"),
        sa.CheckConstraint(
            "length(analysis_key) = 64 AND analysis_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_analysis_key",
        ),
        sa.CheckConstraint(
            "length(source_content_sha256) = 64 AND source_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_source_sha256",
        ),
        sa.CheckConstraint(
            "length(render_sha256) = 64 AND render_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_render_sha256",
        ),
        sa.CheckConstraint(
            "analysis_sha256 IS NULL OR "
            "(length(analysis_sha256) = 64 AND analysis_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_visual_analysis_runs_analysis_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED','TIMED_OUT','UNAVAILABLE')",
            name="ck_visual_analysis_runs_status",
        ),
        sa.CheckConstraint(
            "((status IN ('SUCCEEDED','PARTIAL') AND analysis_sha256 IS NOT NULL) OR "
            "(status IN ('FAILED','TIMED_OUT','UNAVAILABLE') AND analysis_sha256 IS NULL))",
            name="ck_visual_analysis_runs_status_hash",
        ),
        sa.CheckConstraint(
            "length(model_id) BETWEEN 1 AND 128 AND "
            "length(model_revision) BETWEEN 1 AND 128 AND "
            "length(prompt_revision) BETWEEN 1 AND 128 AND "
            "length(selector_revision) BETWEEN 1 AND 128 AND "
            "length(renderer_revision) BETWEEN 1 AND 128 AND "
            "length(analysis_schema_revision) BETWEEN 1 AND 128",
            name="ck_visual_analysis_runs_revisions",
        ),
    )
    op.create_index(
        "uq_visual_analysis_runs_succeeded_key",
        "visual_analysis_runs",
        ["analysis_key"],
        unique=True,
        sqlite_where=sa.text("status = 'SUCCEEDED'"),
    )
    op.create_index(
        "ix_visual_analysis_runs_document_page_source",
        "visual_analysis_runs",
        ["document_version_id", "page_number", "source_content_sha256"],
    )
    op.create_table(
        "visual_elements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_run_id",
            sa.Integer(),
            sa.ForeignKey("visual_analysis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("visual_kind", sa.String(32), nullable=False),
        sa.Column("observed_text", sa.Text(), nullable=True),
        sa.Column("interpretation_text", sa.Text(), nullable=True),
        sa.Column("structured_payload", sa.Text(), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", "sequence", name="uq_visual_elements_run_sequence"),
        sa.CheckConstraint("id > 0", name="ck_visual_elements_id"),
        sa.CheckConstraint("analysis_run_id > 0", name="ck_visual_elements_analysis_run_id"),
        sa.CheckConstraint("sequence > 0", name="ck_visual_elements_sequence"),
        sa.CheckConstraint(
            "visual_kind IN ('TABLE','EQUATION','CHART','DIAGRAM','TEXT_REGION','OTHER')",
            name="ck_visual_elements_kind",
        ),
        sa.CheckConstraint(
            "observed_text IS NOT NULL OR interpretation_text IS NOT NULL OR "
            "structured_payload IS NOT NULL",
            name="ck_visual_elements_content",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_elements_payload_sha256",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_visual_elements_confidence",
        ),
    )


def downgrade() -> None:
    op.drop_table("visual_elements")
    op.drop_index("ix_visual_analysis_runs_document_page_source", table_name="visual_analysis_runs")
    op.drop_index("uq_visual_analysis_runs_succeeded_key", table_name="visual_analysis_runs")
    op.drop_table("visual_analysis_runs")
