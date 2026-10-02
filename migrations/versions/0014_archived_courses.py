"""Mark archived (past) courses and the items archived with them."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_archived_courses"
down_revision = "0013_rag_document_summaries"
branch_labels = None
depends_on = None

_TABLES = ("courses", "assignments", "announcements", "remote_files")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("archived_at", sa.String(40), nullable=True))

    # Backfill: courses already inactive were not listed by the latest
    # complete sync, so they were archived by it. Their inactive items archived
    # with them. Inactive items of current courses stay NULL (removed).
    connection = op.get_bind()
    archived_at = connection.execute(
        sa.text(
            "SELECT finished_at FROM moodle_sync_snapshots "
            "WHERE status = 'COMPLETE' AND finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        )
    ).scalar()
    if archived_at is None:
        return
    connection.execute(
        sa.text("UPDATE courses SET archived_at = :at WHERE is_active = 0"),
        {"at": archived_at},
    )
    for table in _TABLES[1:]:
        connection.execute(
            sa.text(
                f"UPDATE {table} SET archived_at = :at "  # noqa: S608 - fixed table names
                "WHERE is_active = 0 AND course_id IN "
                "(SELECT id FROM courses WHERE archived_at IS NOT NULL)"
            ),
            {"at": archived_at},
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("archived_at")
