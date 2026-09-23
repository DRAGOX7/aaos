"""Add structural RAG provenance.

Revision ID: 0009_rag_structural_provenance
Revises: 0008_document_structural_provenance
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op

revision = "0009_rag_structural_provenance"
down_revision = "0008_document_structural_provenance"
branch_labels = None
depends_on = None


@contextmanager
def _sqlite_fk_rebuild_window() -> Iterator[None]:
    connection = op.get_bind()

    if connection.dialect.name != "sqlite":
        yield
        return

    with op.get_context().autocommit_block():
        connection = op.get_bind()

        enabled = connection.exec_driver_sql(
            "PRAGMA foreign_keys"
        ).scalar_one()
        if enabled != 1:
            raise RuntimeError(
                "SQLite foreign key enforcement must be enabled "
                "before RAG structural provenance migration"
            )

        existing_violation = connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).first()
        if existing_violation is not None:
            raise RuntimeError(
                "Cannot migrate RAG structural provenance with "
                "existing foreign key violations"
            )

        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
        disabled = connection.exec_driver_sql(
            "PRAGMA foreign_keys"
        ).scalar_one()
        if disabled != 0:
            raise RuntimeError(
                "Cannot safely suspend SQLite foreign key enforcement"
            )

        try:
            yield

            violation = connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).first()
            if violation is not None:
                raise RuntimeError(
                    "RAG structural provenance migration produced "
                    "a foreign key violation"
                )
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            restored = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one()
            if restored != 1:
                raise RuntimeError(
                    "SQLite foreign key enforcement was not restored"
                )


def upgrade() -> None:
    with _sqlite_fk_rebuild_window():
        with op.batch_alter_table("rag_chunks") as batch:
            batch.drop_constraint(
                "ck_rag_chunks_page_bounds",
                type_="check",
            )
            batch.add_column(
                sa.Column(
                    "provenance_mode",
                    sa.String(16),
                    nullable=False,
                    server_default="PAGE",
                )
            )
            batch.alter_column(
                "page_start",
                existing_type=sa.Integer(),
                nullable=True,
            )
            batch.alter_column(
                "page_end",
                existing_type=sa.Integer(),
                nullable=True,
            )
            batch.create_check_constraint(
                "ck_rag_chunks_provenance_mode",
                "provenance_mode IN ('PAGE', 'STRUCTURE')",
            )
            batch.create_check_constraint(
                "ck_rag_chunks_provenance_shape",
                "("
                "provenance_mode = 'PAGE' "
                "AND page_start IS NOT NULL "
                "AND page_end IS NOT NULL "
                "AND page_start > 0 "
                "AND page_end >= page_start"
                ") OR ("
                "provenance_mode = 'STRUCTURE' "
                "AND page_start IS NULL "
                "AND page_end IS NULL"
                ")",
            )

        op.create_table(
            "rag_chunk_structure_source_spans",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "rag_chunk_id",
                sa.Integer(),
                sa.ForeignKey("rag_chunks.id"),
                nullable=False,
            ),
            sa.Column(
                "document_structure_unit_id",
                sa.Integer(),
                sa.ForeignKey("document_structure_units.id"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column(
                "source_char_start",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "source_char_end",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "chunk_char_start",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "chunk_char_end",
                sa.Integer(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "rag_chunk_id",
                "ordinal",
                name=(
                    "uq_rag_chunk_structure_source_spans_"
                    "chunk_ordinal"
                ),
            ),
            sa.CheckConstraint(
                "id > 0",
                name="ck_rag_chunk_structure_source_spans_id",
            ),
            sa.CheckConstraint(
                "rag_chunk_id > 0",
                name=(
                    "ck_rag_chunk_structure_source_spans_"
                    "chunk_id"
                ),
            ),
            sa.CheckConstraint(
                "document_structure_unit_id > 0",
                name=(
                    "ck_rag_chunk_structure_source_spans_"
                    "unit_id"
                ),
            ),
            sa.CheckConstraint(
                "ordinal > 0",
                name=(
                    "ck_rag_chunk_structure_source_spans_"
                    "ordinal"
                ),
            ),
            sa.CheckConstraint(
                "source_char_start >= 0 "
                "AND source_char_end > source_char_start",
                name=(
                    "ck_rag_chunk_structure_source_spans_"
                    "source_bounds"
                ),
            ),
            sa.CheckConstraint(
                "chunk_char_start >= 0 "
                "AND chunk_char_end > chunk_char_start",
                name=(
                    "ck_rag_chunk_structure_source_spans_"
                    "chunk_bounds"
                ),
            ),
        )

        op.create_index(
            "ix_rag_chunk_structure_source_spans_chunk_ordinal",
            "rag_chunk_structure_source_spans",
            ["rag_chunk_id", "ordinal"],
        )


def _assert_downgrade_compatible() -> None:
    connection = op.get_bind()

    structural_chunk = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM rag_chunks
            WHERE provenance_mode != 'PAGE'
               OR page_start IS NULL
               OR page_end IS NULL
            LIMIT 1
            """
        )
    ).first()

    structural_span = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM rag_chunk_structure_source_spans
            LIMIT 1
            """
        )
    ).first()

    if structural_chunk is not None or structural_span is not None:
        raise RuntimeError(
            "Cannot downgrade RAG structural provenance while "
            "STRUCTURE chunk data exists"
        )


def downgrade() -> None:
    _assert_downgrade_compatible()

    with _sqlite_fk_rebuild_window():
        op.drop_index(
            "ix_rag_chunk_structure_source_spans_chunk_ordinal",
            table_name="rag_chunk_structure_source_spans",
        )
        op.drop_table("rag_chunk_structure_source_spans")

        with op.batch_alter_table("rag_chunks") as batch:
            batch.drop_constraint(
                "ck_rag_chunks_provenance_shape",
                type_="check",
            )
            batch.drop_constraint(
                "ck_rag_chunks_provenance_mode",
                type_="check",
            )
            batch.alter_column(
                "page_start",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch.alter_column(
                "page_end",
                existing_type=sa.Integer(),
                nullable=False,
            )
            batch.drop_column("provenance_mode")
            batch.create_check_constraint(
                "ck_rag_chunks_page_bounds",
                "page_start > 0 AND page_end >= page_start",
            )
