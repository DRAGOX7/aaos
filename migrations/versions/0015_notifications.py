"""Phase 10 notification state, events and deliveries (spec §3)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_notifications"
down_revision = "0014_archived_courses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_state",
        sa.Column("source_system", sa.String(100), primary_key=True),
        sa.Column("enabled_at", sa.String(40), nullable=True),
        sa.Column("baseline_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("last_processed_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.false()),
        # The last COMPLETE snapshot an AUTH_REQUIRED alert was sent after:
        # one alert per expiry episode.
        sa.Column("auth_alert_after_snapshot_id", sa.Integer(), nullable=True),
    )
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("course_source_id", sa.String(255), nullable=True),
        sa.Column("entity_source_id", sa.String(255), nullable=True),
        sa.Column("from_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("to_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint(
            "kind IN ('NEW_ANNOUNCEMENT', 'NEW_ASSIGNMENT', 'DEADLINE_CHANGED', "
            "'NEW_COURSE', 'COURSE_ARCHIVED', 'AUTH_REQUIRED')",
            name="ck_notification_events_kind",
        ),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("notification_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.String(40), nullable=True),
        sa.Column("last_error_code", sa.String(32), nullable=True),
        sa.Column("sent_at", sa.String(40), nullable=True),
        sa.UniqueConstraint("event_id", "channel", name="uq_notification_deliveries_event_channel"),
        sa.CheckConstraint("channel IN ('TELEGRAM')", name="ck_notification_deliveries_channel"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SUPPRESSED')",
            name="ck_notification_deliveries_status",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ('TOKEN_INVALID', 'CHAT_FORBIDDEN', "
            "'RATE_LIMITED', 'NETWORK', 'TIMEOUT', 'PROVIDER_ERROR', 'MESSAGE_INVALID', "
            "'NOT_LINKED')",
            name="ck_notification_deliveries_error_code",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_pending",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_pending", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_events")
    op.drop_table("notification_state")
