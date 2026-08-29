"""Create Phase 4 local-RAG persistence and the derived FTS5 index."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_phase4_local_rag"
down_revision = "0003_phase3_document_ingestion"
branch_labels = None
depends_on = None


def _utc_datetime() -> sa.TypeDecorator[object]:
    class UTCText(sa.TypeDecorator[object]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def _verify_fts5() -> None:
    connection = op.get_bind()
    try:
        connection.exec_driver_sql("CREATE VIRTUAL TABLE __aaos_fts5_probe USING fts5(content)")
    except sa.exc.DBAPIError as error:
        raise RuntimeError("SQLite FTS5 support is required for the Phase 4 migration") from error
    connection.exec_driver_sql("DROP TABLE __aaos_fts5_probe")


def upgrade() -> None:
    _verify_fts5()
    op.create_table(
        "rag_index_generations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(64), nullable=False),
        sa.Column("chunking_config", sa.Text(), nullable=False),
        sa.Column("lexical_normalizer_version", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("embedding_revision", sa.String(255), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("retrieval_config", sa.Text(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.Column("activated_at", _utc_datetime(), nullable=True),
        sa.CheckConstraint("id > 0", name="ck_rag_index_generations_id"),
        sa.CheckConstraint(
            "status IN ('BUILDING','ACTIVE','RETIRED')", name="ck_rag_index_generations_status"
        ),
        sa.CheckConstraint(
            "length(config_fingerprint) = 64 AND config_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_index_generations_config_fingerprint",
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0", name="ck_rag_index_generations_embedding_dimensions"
        ),
        sa.CheckConstraint(
            "content_revision >= 0", name="ck_rag_index_generations_content_revision"
        ),
    )
    op.create_index(
        "uq_rag_index_generations_one_active",
        "rag_index_generations",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "rag_document_indexes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "generation_id", sa.Integer(), sa.ForeignKey("rag_index_generations.id"), nullable=False
        ),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("coverage_status", sa.String(16), nullable=False),
        sa.Column("searchable_page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.Column("updated_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint(
            "generation_id",
            "document_version_id",
            name="uq_rag_document_indexes_generation_document",
        ),
        sa.CheckConstraint("id > 0", name="ck_rag_document_indexes_id"),
        sa.CheckConstraint("generation_id > 0", name="ck_rag_document_indexes_generation_id"),
        sa.CheckConstraint(
            "document_version_id > 0", name="ck_rag_document_indexes_document_version_id"
        ),
        sa.CheckConstraint(
            "status IN ('PREPARING','READY','NOT_SEARCHABLE','FAILED')",
            name="ck_rag_document_indexes_status",
        ),
        sa.CheckConstraint(
            "coverage_status IN ('FULL','PARTIAL','NONE')",
            name="ck_rag_document_indexes_coverage_status",
        ),
        sa.CheckConstraint(
            "searchable_page_count >= 0", name="ck_rag_document_indexes_searchable_page_count"
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_rag_document_indexes_chunk_count"),
    )
    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chunk_key", sa.String(64), nullable=False),
        sa.Column(
            "generation_id", sa.Integer(), sa.ForeignKey("rag_index_generations.id"), nullable=False
        ),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(1000), nullable=True),
        sa.Column("exact_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint("chunk_key", name="uq_rag_chunks_chunk_key"),
        sa.UniqueConstraint(
            "generation_id",
            "document_version_id",
            "ordinal",
            name="uq_rag_chunks_generation_document_ordinal",
        ),
        sa.CheckConstraint("id > 0", name="ck_rag_chunks_id"),
        sa.CheckConstraint("generation_id > 0", name="ck_rag_chunks_generation_id"),
        sa.CheckConstraint("document_version_id > 0", name="ck_rag_chunks_document_version_id"),
        sa.CheckConstraint(
            "length(chunk_key) = 64 AND chunk_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_chunks_chunk_key",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_rag_chunks_ordinal"),
        sa.CheckConstraint("length(exact_text) > 0", name="ck_rag_chunks_exact_text"),
        sa.CheckConstraint("token_count > 0", name="ck_rag_chunks_token_count"),
        sa.CheckConstraint(
            "length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_chunks_text_sha256",
        ),
        sa.CheckConstraint(
            "page_start > 0 AND page_end >= page_start", name="ck_rag_chunks_page_bounds"
        ),
    )
    op.create_index(
        "ix_rag_chunks_generation_document_ordinal",
        "rag_chunks",
        ["generation_id", "document_version_id", "ordinal"],
    )
    op.create_table(
        "rag_chunk_source_spans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rag_chunk_id", sa.Integer(), sa.ForeignKey("rag_chunks.id"), nullable=False),
        sa.Column(
            "document_page_id", sa.Integer(), sa.ForeignKey("document_pages.id"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_char_start", sa.Integer(), nullable=False),
        sa.Column("page_char_end", sa.Integer(), nullable=False),
        sa.Column("chunk_char_start", sa.Integer(), nullable=False),
        sa.Column("chunk_char_end", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "rag_chunk_id", "ordinal", name="uq_rag_chunk_source_spans_chunk_ordinal"
        ),
        sa.CheckConstraint("id > 0", name="ck_rag_chunk_source_spans_id"),
        sa.CheckConstraint("rag_chunk_id > 0", name="ck_rag_chunk_source_spans_chunk_id"),
        sa.CheckConstraint(
            "document_page_id > 0", name="ck_rag_chunk_source_spans_document_page_id"
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_rag_chunk_source_spans_ordinal"),
        sa.CheckConstraint("page_number > 0", name="ck_rag_chunk_source_spans_page_number"),
        sa.CheckConstraint(
            "page_char_start >= 0 AND page_char_end > page_char_start",
            name="ck_rag_chunk_source_spans_page_bounds",
        ),
        sa.CheckConstraint(
            "chunk_char_start >= 0 AND chunk_char_end > chunk_char_start",
            name="ck_rag_chunk_source_spans_chunk_bounds",
        ),
    )
    op.create_index(
        "ix_rag_chunk_source_spans_chunk_ordinal",
        "rag_chunk_source_spans",
        ["rag_chunk_id", "ordinal"],
    )
    op.execute(
        "CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(normalized_text, generation_id UNINDEXED, document_version_id UNINDEXED)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks_fts")
    op.drop_index("ix_rag_chunk_source_spans_chunk_ordinal", table_name="rag_chunk_source_spans")
    op.drop_table("rag_chunk_source_spans")
    op.drop_index("ix_rag_chunks_generation_document_ordinal", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_table("rag_document_indexes")
    op.drop_index("uq_rag_index_generations_one_active", table_name="rag_index_generations")
    op.drop_table("rag_index_generations")
