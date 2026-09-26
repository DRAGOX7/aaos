"""Add structural document provenance.

Revision ID: 0008_document_structural_provenance
Revises: 0007_phase8_visual_provenance
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0008_document_structural_provenance"
down_revision = "0007_phase8_visual_provenance"
branch_labels = None
depends_on = None


def _utc_datetime() -> sa.TypeDecorator[object]:
    class UTCText(sa.TypeDecorator[object]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


@contextmanager
def _sqlite_fk_rebuild_window() -> Iterator[None]:
    connection = op.get_bind()

    if connection.dialect.name != "sqlite":
        yield
        return

    # SQLite does not allow PRAGMA foreign_keys to change while a
    # transaction is active. Alembic's autocommit block gives this
    # narrowly scoped schema-rebuild window a valid PRAGMA boundary.
    with op.get_context().autocommit_block():
        connection = op.get_bind()

        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if enabled != 1:
            raise RuntimeError(
                "SQLite foreign key enforcement must be enabled "
                "before structural provenance migration"
            )

        existing_violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
        if existing_violation is not None:
            raise RuntimeError(
                "Cannot migrate structural provenance with existing foreign key violations"
            )

        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
        disabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if disabled != 0:
            raise RuntimeError("Cannot safely suspend SQLite foreign key enforcement")

        try:
            yield

            violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
            if violation is not None:
                raise RuntimeError(
                    "Structural provenance migration produced a foreign key violation"
                )
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            restored = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            if restored != 1:
                raise RuntimeError("SQLite foreign key enforcement was not restored")


def upgrade() -> None:
    with _sqlite_fk_rebuild_window():
        with op.batch_alter_table("content_blobs") as batch:
            batch.drop_constraint(
                "ck_content_blobs_media_type",
                type_="check",
            )
            batch.drop_constraint(
                "ck_content_blobs_storage_key",
                type_="check",
            )
            batch.create_check_constraint(
                "ck_content_blobs_media_type",
                "media_type IN ("
                "'application/pdf', "
                "'application/vnd.openxmlformats-officedocument.wordprocessingml.document'"
                ")",
            )
            batch.create_check_constraint(
                "ck_content_blobs_storage_key",
                "(("
                "media_type = 'application/pdf' "
                "AND length(storage_key) = 78 "
                "AND substr(storage_key, 75, 4) = '.pdf'"
                ") OR ("
                "media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
                "AND length(storage_key) = 79 "
                "AND substr(storage_key, 75, 5) = '.docx'"
                ")) "
                "AND substr(storage_key, 1, 7) = 'sha256/' "
                "AND substr(storage_key, 10, 1) = '/' "
                "AND substr(storage_key, 8, 2) "
                "NOT GLOB '*[^0-9a-f]*' "
                "AND substr(storage_key, 11, 64) "
                "NOT GLOB '*[^0-9a-f]*' "
                "AND substr(storage_key, 8, 2) = "
                "substr(storage_key, 11, 2)",
            )

        with op.batch_alter_table("document_versions") as batch:
            batch.add_column(
                sa.Column(
                    "provenance_mode",
                    sa.String(16),
                    nullable=False,
                    server_default=sa.text("'PAGE'"),
                )
            )
            batch.alter_column(
                "page_count",
                existing_type=sa.Integer(),
                nullable=True,
            )
            batch.create_check_constraint(
                "ck_document_versions_provenance_mode",
                "provenance_mode IN ('PAGE', 'STRUCTURE')",
            )
            batch.create_check_constraint(
                "ck_document_versions_provenance_shape",
                "("
                "provenance_mode = 'PAGE' "
                "AND page_count IS NOT NULL "
                "AND page_count > 0"
                ") OR ("
                "provenance_mode = 'STRUCTURE' "
                "AND page_count IS NULL"
                ")",
            )

    op.create_table(
        "document_structure_units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey(
                "document_versions.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading_level", sa.Integer(), nullable=True),
        sa.Column("table_index", sa.Integer(), nullable=True),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("column_index", sa.Integer(), nullable=True),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint(
            "document_version_id",
            "ordinal",
            name="uq_document_structure_units_version_ordinal",
        ),
        sa.CheckConstraint(
            "id > 0",
            name="ck_document_structure_units_id",
        ),
        sa.CheckConstraint(
            "document_version_id > 0",
            name="ck_document_structure_units_document_version_id",
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_document_structure_units_ordinal",
        ),
        sa.CheckConstraint(
            "kind IN ('HEADING', 'PARAGRAPH', 'TABLE_CELL')",
            name="ck_document_structure_units_kind",
        ),
        sa.CheckConstraint(
            "("
            "kind = 'HEADING' "
            "AND heading_level BETWEEN 1 AND 9 "
            "AND table_index IS NULL "
            "AND row_index IS NULL "
            "AND column_index IS NULL"
            ") OR ("
            "kind = 'PARAGRAPH' "
            "AND heading_level IS NULL "
            "AND table_index IS NULL "
            "AND row_index IS NULL "
            "AND column_index IS NULL"
            ") OR ("
            "kind = 'TABLE_CELL' "
            "AND heading_level IS NULL "
            "AND table_index IS NOT NULL "
            "AND table_index >= 0 "
            "AND row_index IS NOT NULL "
            "AND row_index >= 0 "
            "AND column_index IS NOT NULL "
            "AND column_index >= 0"
            ")",
            name="ck_document_structure_units_shape",
        ),
    )
    op.create_index(
        "ix_document_structure_units_version_ordinal",
        "document_structure_units",
        ["document_version_id", "ordinal"],
    )


def _assert_downgrade_compatible() -> None:
    connection = op.get_bind()

    incompatible_version = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM document_versions
            WHERE provenance_mode IS NULL
               OR provenance_mode != 'PAGE'
               OR page_count IS NULL
            LIMIT 1
            """
        )
    ).first()

    structural_unit = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM document_structure_units
            LIMIT 1
            """
        )
    ).first()

    incompatible_blob = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM content_blobs
            WHERE media_type IS NULL
               OR media_type != 'application/pdf'
            LIMIT 1
            """
        )
    ).first()

    if (
        incompatible_version is not None
        or structural_unit is not None
        or incompatible_blob is not None
    ):
        raise RuntimeError(
            "Cannot downgrade structural provenance while STRUCTURE or non-PDF data exists"
        )


def downgrade() -> None:
    _assert_downgrade_compatible()

    with _sqlite_fk_rebuild_window():
        op.drop_index(
            "ix_document_structure_units_version_ordinal",
            table_name="document_structure_units",
        )
        op.drop_table("document_structure_units")

        with op.batch_alter_table("document_versions") as batch:
            batch.drop_constraint(
                "ck_document_versions_provenance_shape",
                type_="check",
            )
            batch.drop_constraint(
                "ck_document_versions_provenance_mode",
                type_="check",
            )
            batch.alter_column(
                "page_count",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch.drop_column("provenance_mode")
        with op.batch_alter_table("content_blobs") as batch:
            batch.drop_constraint(
                "ck_content_blobs_storage_key",
                type_="check",
            )
            batch.drop_constraint(
                "ck_content_blobs_media_type",
                type_="check",
            )
            batch.create_check_constraint(
                "ck_content_blobs_media_type",
                "media_type = 'application/pdf'",
            )
            batch.create_check_constraint(
                "ck_content_blobs_storage_key",
                "length(storage_key) = 78 "
                "AND substr(storage_key, 1, 7) = 'sha256/' "
                "AND substr(storage_key, 10, 1) = '/' "
                "AND substr(storage_key, 75, 4) = '.pdf' "
                "AND substr(storage_key, 8, 2) "
                "NOT GLOB '*[^0-9a-f]*' "
                "AND substr(storage_key, 11, 64) "
                "NOT GLOB '*[^0-9a-f]*' "
                "AND substr(storage_key, 8, 2) = "
                "substr(storage_key, 11, 2)",
            )
