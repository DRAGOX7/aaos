"""Create the Phase 6A durable approval ledger.

Revision ID: 0005_phase6a_approval_core
Revises: 0004_phase4_local_rag
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase6a_approval_core"
down_revision: str | Sequence[str] | None = "0004_phase4_local_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _utc_datetime() -> sa.TypeDecorator[str]:
    class UTCText(sa.TypeDecorator[str]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def _hash_check(column: str) -> str:
    return f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'"


def upgrade() -> None:
    op.create_table(
        "prepared_actions",
        sa.Column("action_id", sa.String(128), primary_key=True),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("actor_identity", sa.String(128), nullable=False),
        sa.Column("target_identity", sa.Text(), nullable=False),
        sa.Column("action_type", sa.String(128), nullable=False),
        sa.Column("canonical_arguments", sa.LargeBinary(), nullable=False),
        sa.Column("arguments_sha256", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_schema_version", sa.String(128), nullable=False),
        sa.Column("tool_definition_fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(128), nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("remote_precondition", sa.Text(), nullable=False),
        sa.Column("prepared_at", _utc_datetime(), nullable=False),
        sa.Column("action_expires_at", _utc_datetime(), nullable=False),
        sa.Column("prepared_action_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_prepared_actions_operation_id"),
        sa.UniqueConstraint(
            "action_id", "operation_id", name="uq_prepared_actions_action_operation"
        ),
        sa.CheckConstraint(
            _hash_check("arguments_sha256"), name="ck_prepared_actions_arguments_sha256"
        ),
        sa.CheckConstraint(
            _hash_check("tool_definition_fingerprint"),
            name="ck_prepared_actions_tool_definition_fingerprint",
        ),
        sa.CheckConstraint(
            _hash_check("policy_fingerprint"), name="ck_prepared_actions_policy_fingerprint"
        ),
        sa.CheckConstraint(
            _hash_check("prepared_action_sha256"),
            name="ck_prepared_actions_prepared_action_sha256",
        ),
        sa.CheckConstraint(
            "length(canonical_arguments) > 0",
            name="ck_prepared_actions_canonical_arguments",
        ),
        sa.CheckConstraint(
            "json_valid(target_identity) AND length(target_identity) <= 16384",
            name="ck_prepared_actions_target_identity",
        ),
        sa.CheckConstraint(
            "json_valid(remote_precondition) AND length(remote_precondition) <= 16384",
            name="ck_prepared_actions_remote_precondition",
        ),
        sa.CheckConstraint("action_expires_at > prepared_at", name="ck_prepared_actions_expiry"),
    )
    op.create_index("ix_prepared_actions_actor", "prepared_actions", ["actor_identity"])
    op.create_index("ix_prepared_actions_action_type", "prepared_actions", ["action_type"])

    op.create_table(
        "action_lifecycles",
        sa.Column(
            "action_id",
            sa.String(128),
            sa.ForeignKey("prepared_actions.action_id"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.Column("updated_at", _utc_datetime(), nullable=False),
        sa.Column("terminal_at", _utc_datetime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('PREPARED','AWAITING_APPROVAL','APPROVED','CLAIMED','EXECUTING',"
            "'RECONCILIATION_PENDING','UNKNOWN_OUTCOME','CONFIRMED_SUCCESS',"
            "'CONFIRMED_FAILURE','REJECTED','INVALIDATED','EXPIRED','REVOKED')",
            name="ck_action_lifecycles_state",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_action_lifecycles_revision"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_action_lifecycles_updated_at"),
    )
    op.create_index("ix_action_lifecycles_state", "action_lifecycles", ["state"])

    op.create_table(
        "approval_records",
        sa.Column("approval_id", sa.String(128), primary_key=True),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("approver_identity", sa.String(128), nullable=False),
        sa.Column("approver_context_id", sa.String(128), nullable=False),
        sa.Column("prepared_action_sha256", sa.String(64), nullable=False),
        sa.Column("approval_binding_sha256", sa.String(64), nullable=False),
        sa.Column("issued_at", _utc_datetime(), nullable=False),
        sa.Column("approval_expires_at", _utc_datetime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_at", _utc_datetime(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_approval_records_prepared_action",
        ),
        sa.UniqueConstraint(
            "approval_id",
            "action_id",
            "operation_id",
            name="uq_approval_records_identity_binding",
        ),
        sa.CheckConstraint(
            _hash_check("prepared_action_sha256"),
            name="ck_approval_records_prepared_action_sha256",
        ),
        sa.CheckConstraint(
            _hash_check("approval_binding_sha256"),
            name="ck_approval_records_approval_binding_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','CONSUMED','REJECTED','REVOKED','EXPIRED')",
            name="ck_approval_records_status",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('USER_APPROVED','USER_REJECTED','USER_REVOKED','ACTION_EXPIRED',"
            "'CAPABILITY_REVOKED','SCHEMA_VERSION_DRIFT','POLICY_VERSION_DRIFT',"
            "'BINDING_DRIFT','STORED_PRECONDITION_DRIFT')",
            name="ck_approval_records_reason_code",
        ),
        sa.CheckConstraint("approval_expires_at > issued_at", name="ck_approval_records_expiry"),
    )
    op.create_index(
        "ix_approval_records_action_status", "approval_records", ["action_id", "status"]
    )

    op.create_table(
        "approval_consumptions",
        sa.Column("approval_id", sa.String(128), primary_key=True),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("claim_identity", sa.String(128), nullable=False),
        sa.Column("consumed_at", _utc_datetime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id", "action_id", "operation_id"],
            [
                "approval_records.approval_id",
                "approval_records.action_id",
                "approval_records.operation_id",
            ],
            name="fk_approval_consumptions_approval_binding",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_approval_consumptions_prepared_action",
        ),
        sa.UniqueConstraint("operation_id", name="uq_approval_consumptions_operation_id"),
    )
    op.create_index("ix_approval_consumptions_action", "approval_consumptions", ["action_id"])

    op.create_table(
        "operation_records",
        sa.Column("operation_id", sa.String(128), primary_key=True),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("outcome_kind", sa.String(32), nullable=True),
        sa.Column("receipt_evidence", sa.Text(), nullable=True),
        sa.Column("dispatch_started_at", _utc_datetime(), nullable=True),
        sa.Column("outcome_recorded_at", _utc_datetime(), nullable=True),
        sa.Column("reconciliation_at", _utc_datetime(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_id", "operation_id"],
            ["prepared_actions.action_id", "prepared_actions.operation_id"],
            name="fk_operation_records_prepared_action",
        ),
        sa.UniqueConstraint("action_id", name="uq_operation_records_action_id"),
        sa.CheckConstraint(
            "state IN ('CLAIMED','EXECUTING','NOT_DISPATCHED','CONFIRMED_SUCCESS',"
            "'CONFIRMED_FAILURE','UNKNOWN_OUTCOME','RECONCILIATION_PENDING')",
            name="ck_operation_records_state",
        ),
        sa.CheckConstraint(
            "outcome_kind IS NULL OR outcome_kind IN "
            "('CONFIRMED_SUCCESS','CONFIRMED_FAILURE','UNKNOWN_OUTCOME')",
            name="ck_operation_records_outcome_kind",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_operation_records_revision"),
        sa.CheckConstraint(
            "receipt_evidence IS NULL OR length(receipt_evidence) <= 2048",
            name="ck_operation_records_receipt_evidence",
        ),
    )
    op.create_index("ix_operation_records_state", "operation_records", ["state"])

    op.create_table(
        "action_event_counters",
        sa.Column(
            "action_id",
            sa.String(128),
            sa.ForeignKey("prepared_actions.action_id"),
            primary_key=True,
        ),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint("next_sequence >= 0", name="ck_action_event_counters_next_sequence"),
    )

    op.create_table(
        "action_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column(
            "action_id",
            sa.String(128),
            sa.ForeignKey("prepared_actions.action_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_kind", sa.String(64), nullable=False),
        sa.Column("detail_payload", sa.Text(), nullable=False),
        sa.Column("occurred_at", _utc_datetime(), nullable=False),
        sa.Column("actor_identity", sa.String(128), nullable=False),
        sa.UniqueConstraint("action_id", "sequence", name="uq_action_events_action_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_action_events_sequence"),
        sa.CheckConstraint(
            "event_kind IN ('ACTION_PREPARED','APPROVAL_REQUESTED','APPROVAL_DECIDED',"
            "'APPROVAL_CONSUMED','DISPATCH_STARTED','OUTCOME_RECORDED',"
            "'RECONCILIATION_RECORDED','ACTION_INVALIDATED','LATE_RESULT_IGNORED')",
            name="ck_action_events_event_kind",
        ),
        sa.CheckConstraint(
            "json_valid(detail_payload) AND length(detail_payload) <= 16384",
            name="ck_action_events_detail_payload",
        ),
    )
    op.create_index("ix_action_events_action_sequence", "action_events", ["action_id", "sequence"])

    op.execute(
        "CREATE TRIGGER phase6a_prepared_actions_no_update "
        "BEFORE UPDATE ON prepared_actions "
        "BEGIN SELECT RAISE(ABORT, 'immutable prepared action'); END"
    )
    op.execute(
        "CREATE TRIGGER phase6a_prepared_actions_no_delete "
        "BEFORE DELETE ON prepared_actions "
        "BEGIN SELECT RAISE(ABORT, 'immutable prepared action'); END"
    )
    op.execute(
        "CREATE TRIGGER approval_expiry_guard_insert "
        "BEFORE INSERT ON approval_records "
        "WHEN NEW.approval_expires_at > ("
        "SELECT action_expires_at FROM prepared_actions WHERE action_id = NEW.action_id"
        ") BEGIN SELECT RAISE(ABORT, 'approval expiry exceeds action expiry'); END"
    )
    op.execute(
        "CREATE TRIGGER approval_expiry_guard_update "
        "BEFORE UPDATE OF approval_expires_at, action_id ON approval_records "
        "WHEN NEW.approval_expires_at > ("
        "SELECT action_expires_at FROM prepared_actions WHERE action_id = NEW.action_id"
        ") BEGIN SELECT RAISE(ABORT, 'approval expiry exceeds action expiry'); END"
    )
    op.execute(
        "CREATE TRIGGER phase6a_action_events_no_update "
        "BEFORE UPDATE ON action_events "
        "BEGIN SELECT RAISE(ABORT, 'append-only action event'); END"
    )
    op.execute(
        "CREATE TRIGGER phase6a_action_events_no_delete "
        "BEFORE DELETE ON action_events "
        "BEGIN SELECT RAISE(ABORT, 'append-only action event'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS phase6a_action_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS phase6a_action_events_no_update")
    op.execute("DROP TRIGGER IF EXISTS approval_expiry_guard_update")
    op.execute("DROP TRIGGER IF EXISTS approval_expiry_guard_insert")
    op.execute("DROP TRIGGER IF EXISTS phase6a_prepared_actions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS phase6a_prepared_actions_no_update")
    op.drop_index("ix_action_events_action_sequence", table_name="action_events")
    op.drop_table("action_events")
    op.drop_table("action_event_counters")
    op.drop_index("ix_operation_records_state", table_name="operation_records")
    op.drop_table("operation_records")
    op.drop_index("ix_approval_consumptions_action", table_name="approval_consumptions")
    op.drop_table("approval_consumptions")
    op.drop_index("ix_approval_records_action_status", table_name="approval_records")
    op.drop_table("approval_records")
    op.drop_index("ix_action_lifecycles_state", table_name="action_lifecycles")
    op.drop_table("action_lifecycles")
    op.drop_index("ix_prepared_actions_action_type", table_name="prepared_actions")
    op.drop_index("ix_prepared_actions_actor", table_name="prepared_actions")
    op.drop_table("prepared_actions")
