"""Create the Phase 1 academic domain tables.

Revision ID: 0002_phase1_academic_domain
Revises: 0001_baseline
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase1_academic_domain"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _utc_datetime() -> sa.TypeDecorator[str]:
    class UTCText(sa.TypeDecorator[str]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def _common_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_updated_at", _utc_datetime(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "courses",
        *_common_columns(),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("code", sa.String(255), nullable=True),
        sa.Column("description", sa.String(4000), nullable=True),
        sa.UniqueConstraint("source_system", "source_id", name="uq_courses_source_identity"),
    )
    for table, title_size, extra in (
        (
            "assignments",
            500,
            [
                sa.Column("description", sa.String(4000), nullable=True),
                sa.Column("due_at", _utc_datetime(), nullable=True),
            ],
        ),
        (
            "announcements",
            500,
            [
                sa.Column("body", sa.String(8000), nullable=True),
                sa.Column("published_at", _utc_datetime(), nullable=True),
            ],
        ),
        ("remote_files", 500, [sa.Column("remote_url", sa.String(2000), nullable=True)]),
    ):
        op.create_table(
            table,
            *_common_columns(),
            sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
            sa.Column("title", sa.String(title_size), nullable=False),
            *extra,
            sa.UniqueConstraint("source_system", "source_id", name=f"uq_{table}_source_identity"),
        )
        op.create_index(f"ix_{table}_course_active", table, ["course_id", "is_active"])
        op.create_index(f"ix_{table}_source_active", table, ["source_system", "is_active"])
    op.create_index("ix_courses_source_active", "courses", ["source_system", "is_active"])


def downgrade() -> None:
    for table in ("remote_files", "announcements", "assignments"):
        op.drop_index(f"ix_{table}_source_active", table_name=table)
        op.drop_index(f"ix_{table}_course_active", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_courses_source_active", table_name="courses")
    op.drop_table("courses")
