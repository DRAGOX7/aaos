"""Add the Phase 7E durable browser task ledger."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_phase7e_task_records"
down_revision = "0005_phase6a_approval_core"
branch_labels = None
depends_on = None


def _utc_datetime() -> sa.TypeDecorator[object]:
    class UTCText(sa.TypeDecorator[object]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def upgrade() -> None:
    op.create_table(
        "task_records",
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("task_kind", sa.String(64), nullable=False),
        sa.Column("actor_identity", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("creation_state", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "action_id",
            sa.String(128),
            sa.ForeignKey("prepared_actions.action_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.Column("updated_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint("actor_identity", "idempotency_key", name="uq_task_records_actor_key"),
        sa.UniqueConstraint("action_id", name="uq_task_records_action_id"),
        sa.CheckConstraint(
            "task_kind IN ('CALENDAR_CREATE_EVENT')", name="ck_task_records_task_kind"
        ),
        sa.CheckConstraint(
            "creation_state IN ('CREATED','ACTION_BOUND','FAILED')",
            name="ck_task_records_creation_state",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('INVALID_REQUEST','CAPABILITY_DENIED','PREPARATION_FAILED')",
            name="ck_task_records_failure_code",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_task_records_request_fingerprint",
        ),
    )
    op.create_index("ix_task_records_created_at", "task_records", ["created_at"])
    op.create_index("ix_task_records_creation_state", "task_records", ["creation_state"])


def downgrade() -> None:
    op.drop_index("ix_task_records_creation_state", table_name="task_records")
    op.drop_index("ix_task_records_created_at", table_name="task_records")
    op.drop_table("task_records")
