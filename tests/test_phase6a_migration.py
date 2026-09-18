"""Migration tests for the Phase 6A durable approval ledger."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from aaos.persistence.database import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE6A_REVISION = "0005_phase6a_approval_core"
PHASE4_REVISION = "0004_phase4_local_rag"
LEDGER_TABLES = {
    "prepared_actions",
    "action_lifecycles",
    "approval_records",
    "approval_consumptions",
    "operation_records",
    "action_event_counters",
    "action_events",
}

ACTION_ID = "action-1"
OPERATION_ID = "operation-1"
APPROVAL_ID = "approval-1"
EVENT_ID = "event-1"
ACTION_EXPIRY = "2026-09-12T12:00:00Z"
ACTION_EXPIRY_PLUS_ONE = "2026-09-12T12:00:01Z"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def make_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def revision(database_path: Path) -> str | None:
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def sqlite_master_names(connection: Connection, *, kind: str = "table") -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type=:kind"), {"kind": kind}
        )
    }


def upgrade(database_path: Path) -> Engine:
    command.upgrade(make_config(database_path), "head")
    return create_database_engine(database_path)


@contextmanager
def phase6a_database(database_path: Path) -> Iterator[Engine]:
    engine = upgrade(database_path)
    try:
        yield engine
    finally:
        engine.dispose()


def prepared_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "action_id": ACTION_ID,
        "operation_id": OPERATION_ID,
        "actor_identity": "actor-1",
        "target_identity": '{"account":"account-1","provider":"fake","resource":"item-1"}',
        "action_type": "fake.write",
        "canonical_arguments": b'{"message":"hello"}',
        "arguments_sha256": HASH_A,
        "tool_name": "fake_write",
        "tool_schema_version": "v1",
        "tool_definition_fingerprint": HASH_B,
        "policy_version": "v1",
        "policy_fingerprint": HASH_C,
        "remote_precondition": (
            '{"etag":"etag-1","provider":"fake","resource":"item-1","version":"v1"}'
        ),
        "prepared_at": "2026-09-12T10:00:00Z",
        "action_expires_at": ACTION_EXPIRY,
        "prepared_action_sha256": HASH_D,
    }
    values.update(overrides)
    return values


def insert_prepared(connection: Connection, **overrides: object) -> None:
    connection.execute(
        text(
            "INSERT INTO prepared_actions "
            "(action_id, operation_id, actor_identity, target_identity, action_type, "
            "canonical_arguments, arguments_sha256, tool_name, tool_schema_version, "
            "tool_definition_fingerprint, policy_version, policy_fingerprint, "
            "remote_precondition, prepared_at, action_expires_at, prepared_action_sha256) "
            "VALUES (:action_id, :operation_id, :actor_identity, :target_identity, "
            ":action_type, :canonical_arguments, :arguments_sha256, :tool_name, "
            ":tool_schema_version, :tool_definition_fingerprint, :policy_version, "
            ":policy_fingerprint, :remote_precondition, :prepared_at, "
            ":action_expires_at, :prepared_action_sha256)"
        ),
        prepared_values(**overrides),
    )


def approval_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "action_id": ACTION_ID,
        "operation_id": OPERATION_ID,
        "approver_identity": "approver-1",
        "approver_context_id": "context-1",
        "prepared_action_sha256": HASH_D,
        "approval_binding_sha256": "e" * 64,
        "issued_at": "2026-09-12T10:30:00Z",
        "approval_expires_at": "2026-09-12T11:30:00Z",
        "status": "PENDING",
        "decided_at": None,
        "reason_code": None,
    }
    values.update(overrides)
    return values


def insert_approval(connection: Connection, **overrides: object) -> None:
    connection.execute(
        text(
            "INSERT INTO approval_records "
            "(approval_id, action_id, operation_id, approver_identity, approver_context_id, "
            "prepared_action_sha256, approval_binding_sha256, issued_at, approval_expires_at, "
            "status, decided_at, reason_code) "
            "VALUES (:approval_id, :action_id, :operation_id, :approver_identity, "
            ":approver_context_id, :prepared_action_sha256, :approval_binding_sha256, "
            ":issued_at, :approval_expires_at, :status, :decided_at, :reason_code)"
        ),
        approval_values(**overrides),
    )


def insert_event(connection: Connection, **overrides: object) -> None:
    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "action_id": ACTION_ID,
        "sequence": 0,
        "event_kind": "ACTION_PREPARED",
        "detail_payload": '{"prepared_action_sha256":"' + HASH_D + '"}',
        "occurred_at": "2026-09-12T10:00:00Z",
        "actor_identity": "actor-1",
    }
    values.update(overrides)
    connection.execute(
        text(
            "INSERT INTO action_events "
            "(event_id, action_id, sequence, event_kind, detail_payload, occurred_at, "
            "actor_identity) VALUES (:event_id, :action_id, :sequence, :event_kind, "
            ":detail_payload, :occurred_at, :actor_identity)"
        ),
        values,
    )


def test_phase6a_upgrade_from_phase4_creates_all_ledger_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    config = make_config(database_path)
    command.upgrade(config, PHASE4_REVISION)

    command.upgrade(config, PHASE6A_REVISION)

    assert revision(database_path) == PHASE6A_REVISION
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            assert sqlite_master_names(connection) >= LEDGER_TABLES
    finally:
        engine.dispose()


def test_phase6a_schema_has_exact_task3_columns_and_no_attempt_or_catch_all(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        inspector = inspect(engine)
        expected_columns = {
            "prepared_actions": {
                "action_id",
                "operation_id",
                "actor_identity",
                "target_identity",
                "action_type",
                "canonical_arguments",
                "arguments_sha256",
                "tool_name",
                "tool_schema_version",
                "tool_definition_fingerprint",
                "policy_version",
                "policy_fingerprint",
                "remote_precondition",
                "prepared_at",
                "action_expires_at",
                "prepared_action_sha256",
            },
            "action_lifecycles": {
                "action_id",
                "state",
                "revision",
                "created_at",
                "updated_at",
                "terminal_at",
            },
            "approval_records": {
                "approval_id",
                "action_id",
                "operation_id",
                "approver_identity",
                "approver_context_id",
                "prepared_action_sha256",
                "approval_binding_sha256",
                "issued_at",
                "approval_expires_at",
                "status",
                "decided_at",
                "reason_code",
            },
            "approval_consumptions": {
                "approval_id",
                "action_id",
                "operation_id",
                "claim_identity",
                "consumed_at",
            },
            "operation_records": {
                "operation_id",
                "action_id",
                "state",
                "outcome_kind",
                "receipt_evidence",
                "dispatch_started_at",
                "outcome_recorded_at",
                "reconciliation_at",
                "revision",
            },
            "action_event_counters": {"action_id", "next_sequence"},
            "action_events": {
                "event_id",
                "action_id",
                "sequence",
                "event_kind",
                "detail_payload",
                "occurred_at",
                "actor_identity",
            },
        }
        for table, columns in expected_columns.items():
            assert {column["name"] for column in inspector.get_columns(table)} == columns


def test_phase6a_orm_metadata_discovers_all_ledger_tables() -> None:
    import aaos.persistence.approval_models  # noqa: F401
    from aaos.persistence.academic_models import Base

    assert set(Base.metadata.tables) >= LEDGER_TABLES


def test_phase6a_schema_enforces_foreign_keys_and_one_to_one_identities(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        inspector = inspect(engine)
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("approval_records")
        } == {"prepared_actions"}
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("approval_consumptions")
        } >= {"prepared_actions", "approval_records"}
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("operation_records")
        } == {"prepared_actions"}

        with engine.begin() as connection:
            insert_prepared(connection)
            insert_prepared(
                connection,
                action_id="action-2",
                operation_id="operation-2",
                prepared_action_sha256="f" * 64,
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_prepared(
                    connection,
                    action_id="action-3",
                    operation_id=OPERATION_ID,
                    prepared_action_sha256="f" * 64,
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO action_lifecycles "
                        "(action_id, state, revision, created_at, updated_at) "
                        "VALUES ('missing', 'PREPARED', 0, :at, :at)"
                    ),
                    {"at": "2026-09-12T10:00:00Z"},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_approval(connection, action_id=ACTION_ID, operation_id="operation-2")


def test_phase6a_schema_enforces_closed_states_hashes_and_nonnegative_revisions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)

        invalid_statements = (
            (
                "INSERT INTO action_lifecycles "
                "(action_id, state, revision, created_at, updated_at) "
                "VALUES (:action, 'UNKNOWN', 0, :at, :at)",
                {"action": ACTION_ID, "at": "2026-09-12T10:00:00Z"},
            ),
            (
                "INSERT INTO action_lifecycles "
                "(action_id, state, revision, created_at, updated_at) "
                "VALUES (:action, 'PREPARED', -1, :at, :at)",
                {"action": ACTION_ID, "at": "2026-09-12T10:00:00Z"},
            ),
            (
                "INSERT INTO approval_records "
                "(approval_id, action_id, operation_id, approver_identity, "
                "approver_context_id, prepared_action_sha256, approval_binding_sha256, "
                "issued_at, approval_expires_at, status) VALUES "
                "('approval-bad', :action, :operation, 'approver', 'context', :hash, "
                ":binding, :issued, :expiry, 'UNKNOWN')",
                {
                    "action": ACTION_ID,
                    "operation": OPERATION_ID,
                    "hash": HASH_D,
                    "binding": "e" * 64,
                    "issued": "2026-09-12T10:00:00Z",
                    "expiry": "2026-09-12T11:00:00Z",
                },
            ),
            (
                "INSERT INTO operation_records "
                "(operation_id, action_id, state, revision) "
                "VALUES (:operation, :action, 'CLAIMED', -1)",
                {"operation": OPERATION_ID, "action": ACTION_ID},
            ),
        )
        for statement, parameters in invalid_statements:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement), parameters)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_prepared(
                    connection,
                    action_id="action-bad-hash",
                    operation_id="operation-bad-hash",
                    arguments_sha256="not-a-sha256",
                    prepared_action_sha256="f" * 64,
                )


def test_direct_sql_cannot_update_or_delete_prepared_action(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)

        with pytest.raises(IntegrityError, match="immutable prepared action"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE prepared_actions SET action_type='other' WHERE action_id=:id"),
                    {"id": ACTION_ID},
                )
        with pytest.raises(IntegrityError, match="immutable prepared action"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM prepared_actions WHERE action_id=:id"),
                    {"id": ACTION_ID},
                )


def test_direct_sql_cannot_update_or_delete_action_event(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)
            insert_event(connection)

        with pytest.raises(IntegrityError, match="append-only action event"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE action_events SET event_kind='other' WHERE event_id=:id"),
                    {"id": EVENT_ID},
                )
        with pytest.raises(IntegrityError, match="append-only action event"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM action_events WHERE event_id=:id"), {"id": EVENT_ID}
                )


def test_direct_sql_rejects_approval_expiry_after_action_expiry_on_insert(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                insert_approval(connection, approval_expires_at=ACTION_EXPIRY_PLUS_ONE)


def test_direct_sql_rejects_approval_expiry_after_action_expiry_on_update(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)
            insert_approval(connection)

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE approval_records SET approval_expires_at=:too_late "
                        "WHERE approval_id=:approval"
                    ),
                    {"approval": APPROVAL_ID, "too_late": ACTION_EXPIRY_PLUS_ONE},
                )


def test_local_expiry_checks_order_fractional_utc_timestamps_chronologically(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                prepared_at="2026-09-12T10:00:00Z",
                action_expires_at="2026-09-12T10:00:00.500000Z",
            )
            insert_approval(
                connection,
                issued_at="2026-09-12T10:00:00Z",
                approval_expires_at="2026-09-12T10:00:00.250000Z",
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_prepared(
                    connection,
                    action_id="action-2",
                    operation_id="operation-2",
                    prepared_at="2026-09-12T11:00:00.500000Z",
                    action_expires_at="2026-09-12T11:00:00Z",
                    prepared_action_sha256="f" * 64,
                )

        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_id="action-3",
                operation_id="operation-3",
                action_expires_at="2026-09-12T12:00:00Z",
                prepared_action_sha256="2" * 64,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_approval(
                    connection,
                    approval_id="approval-3",
                    action_id="action-3",
                    operation_id="operation-3",
                    issued_at="2026-09-12T11:00:00.500000Z",
                    approval_expires_at="2026-09-12T11:00:00Z",
                    prepared_action_sha256="2" * 64,
                    approval_binding_sha256="3" * 64,
                )


def test_approval_expiry_insert_guard_orders_fractional_utc_timestamps_chronologically(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_expires_at="2026-09-12T12:00:00.500000Z",
            )
            insert_approval(
                connection,
                issued_at="2026-09-12T11:00:00Z",
                approval_expires_at="2026-09-12T12:00:00Z",
            )
            insert_prepared(
                connection,
                action_id="action-2",
                operation_id="operation-2",
                action_expires_at="2026-09-12T13:00:00Z",
                prepared_action_sha256="f" * 64,
            )

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                insert_approval(
                    connection,
                    approval_id="approval-2",
                    action_id="action-2",
                    operation_id="operation-2",
                    issued_at="2026-09-12T12:00:00Z",
                    approval_expires_at="2026-09-12T13:00:00.500000Z",
                    prepared_action_sha256="f" * 64,
                    approval_binding_sha256="1" * 64,
                )


def test_approval_expiry_update_guard_orders_fractional_utc_timestamps_chronologically(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_expires_at="2026-09-12T12:00:00.500000Z",
            )
            insert_approval(connection, approval_expires_at="2026-09-12T11:00:00Z")
            insert_prepared(
                connection,
                action_id="action-2",
                operation_id="operation-2",
                action_expires_at="2026-09-12T13:00:00Z",
                prepared_action_sha256="f" * 64,
            )
            insert_approval(
                connection,
                approval_id="approval-2",
                action_id="action-2",
                operation_id="operation-2",
                approval_expires_at="2026-09-12T12:00:00Z",
                prepared_action_sha256="f" * 64,
                approval_binding_sha256="1" * 64,
            )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE approval_records SET approval_expires_at=:expiry "
                    "WHERE approval_id=:approval"
                ),
                {"approval": APPROVAL_ID, "expiry": "2026-09-12T12:00:00Z"},
            )

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE approval_records SET approval_expires_at=:expiry "
                        "WHERE approval_id=:approval"
                    ),
                    {
                        "approval": "approval-2",
                        "expiry": "2026-09-12T13:00:00.500000Z",
                    },
                )


def test_local_expiry_checks_preserve_exact_microsecond_ordering(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                prepared_at="2026-09-12T10:00:00.000001Z",
                action_expires_at="2026-09-12T10:00:00.000499Z",
            )
            insert_approval(
                connection,
                issued_at="2026-09-12T10:00:00.000001Z",
                approval_expires_at="2026-09-12T10:00:00.000499Z",
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_prepared(
                    connection,
                    action_id="action-micro-invalid",
                    operation_id="operation-micro-invalid",
                    prepared_at="2026-09-12T11:00:00.000499Z",
                    action_expires_at="2026-09-12T11:00:00.000001Z",
                    prepared_action_sha256="f" * 64,
                )

        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_id="action-approval-micro-invalid",
                operation_id="operation-approval-micro-invalid",
                action_expires_at="2026-09-12T12:00:01Z",
                prepared_action_sha256="2" * 64,
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_approval(
                    connection,
                    approval_id="approval-micro-invalid",
                    action_id="action-approval-micro-invalid",
                    operation_id="operation-approval-micro-invalid",
                    issued_at="2026-09-12T12:00:00.000499Z",
                    approval_expires_at="2026-09-12T12:00:00.000001Z",
                    prepared_action_sha256="2" * 64,
                    approval_binding_sha256="3" * 64,
                )


def test_approval_expiry_insert_guard_preserves_exact_microsecond_ordering(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_expires_at="2026-09-12T12:00:00.000499Z",
            )
            insert_approval(
                connection,
                issued_at="2026-09-12T11:00:00Z",
                approval_expires_at="2026-09-12T12:00:00.000001Z",
            )
            insert_prepared(
                connection,
                action_id="action-micro-later-approval",
                operation_id="operation-micro-later-approval",
                action_expires_at="2026-09-12T13:00:00.000001Z",
                prepared_action_sha256="f" * 64,
            )

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                insert_approval(
                    connection,
                    approval_id="approval-micro-later",
                    action_id="action-micro-later-approval",
                    operation_id="operation-micro-later-approval",
                    issued_at="2026-09-12T12:00:00Z",
                    approval_expires_at="2026-09-12T13:00:00.000499Z",
                    prepared_action_sha256="f" * 64,
                    approval_binding_sha256="1" * 64,
                )


def test_approval_expiry_update_guard_preserves_exact_microsecond_ordering(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(
                connection,
                action_expires_at="2026-09-12T12:00:00.000499Z",
            )
            insert_approval(connection, approval_expires_at="2026-09-12T11:00:00Z")
            insert_prepared(
                connection,
                action_id="action-micro-later-update",
                operation_id="operation-micro-later-update",
                action_expires_at="2026-09-12T13:00:00.000001Z",
                prepared_action_sha256="f" * 64,
            )
            insert_approval(
                connection,
                approval_id="approval-micro-later-update",
                action_id="action-micro-later-update",
                operation_id="operation-micro-later-update",
                approval_expires_at="2026-09-12T12:00:00Z",
                prepared_action_sha256="f" * 64,
                approval_binding_sha256="1" * 64,
            )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE approval_records SET approval_expires_at=:expiry "
                    "WHERE approval_id=:approval"
                ),
                {"approval": APPROVAL_ID, "expiry": "2026-09-12T12:00:00.000001Z"},
            )

        with pytest.raises(IntegrityError, match="approval expiry exceeds action expiry"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE approval_records SET approval_expires_at=:expiry "
                        "WHERE approval_id=:approval"
                    ),
                    {
                        "approval": "approval-micro-later-update",
                        "expiry": "2026-09-12T13:00:00.000499Z",
                    },
                )


def test_phase6a_schema_enforces_consumption_and_event_uniqueness(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)
            insert_approval(connection, status="APPROVED")
            insert_approval(
                connection,
                approval_id="approval-2",
                status="APPROVED",
                approval_binding_sha256="f" * 64,
            )
            connection.execute(
                text(
                    "INSERT INTO approval_consumptions "
                    "(approval_id, action_id, operation_id, claim_identity, consumed_at) "
                    "VALUES (:approval, :action, :operation, 'worker-1', :at)"
                ),
                {
                    "approval": APPROVAL_ID,
                    "action": ACTION_ID,
                    "operation": OPERATION_ID,
                    "at": "2026-09-12T11:00:00Z",
                },
            )
            insert_event(connection)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO approval_consumptions "
                        "(approval_id, action_id, operation_id, claim_identity, consumed_at) "
                        "VALUES ('approval-2', :action, :operation, 'worker-2', :at)"
                    ),
                    {
                        "action": ACTION_ID,
                        "operation": OPERATION_ID,
                        "at": "2026-09-12T11:01:00Z",
                    },
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                insert_event(connection, event_id="event-2")


def test_phase6a_schema_requires_typed_bounded_event_payload(tmp_path: Path) -> None:
    database_path = tmp_path / "phase6a.db"
    with phase6a_database(database_path) as engine:
        with engine.begin() as connection:
            insert_prepared(connection)

        for payload in ("not-json", json.dumps({"value": "x" * 16384})):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    insert_event(connection, detail_payload=payload)


def test_phase6a_downgrade_removes_ledger_and_triggers_but_preserves_phase4(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase6a.db"
    config = make_config(database_path)
    command.upgrade(config, "head")

    command.downgrade(config, PHASE4_REVISION)

    assert revision(database_path) == PHASE4_REVISION
    engine = create_database_engine(database_path)
    try:
        inspector = inspect(engine)
        assert not (set(inspector.get_table_names()) & LEDGER_TABLES)
        assert set(inspector.get_table_names()) >= {
            "rag_index_generations",
            "rag_document_indexes",
            "rag_chunks",
            "rag_chunk_source_spans",
        }
        with engine.connect() as connection:
            trigger_names = sqlite_master_names(connection, kind="trigger")
            assert not any(
                name.startswith("phase6a_") or name.startswith("approval_expiry_guard_")
                for name in trigger_names
            )
    finally:
        engine.dispose()
