"""Create the Phase 3 document-ingestion tables.

Revision ID: 0003_phase3_document_ingestion
Revises: 0002_phase1_academic_domain
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase3_document_ingestion"
down_revision: str | Sequence[str] | None = "0002_phase1_academic_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _utc_datetime() -> sa.TypeDecorator[str]:
    class UTCText(sa.TypeDecorator[str]):
        impl = sa.String(40)
        cache_ok = True

    return UTCText()


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "remote_file_id",
            sa.Integer(),
            sa.ForeignKey("remote_files.id"),
            nullable=False,
        ),
        sa.Column("ingestion_status", sa.String(32), nullable=False),
        sa.Column("last_attempted_at", _utc_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.Column("updated_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint("remote_file_id", name="uq_documents_remote_file_id"),
        sa.CheckConstraint("id > 0", name="ck_documents_id"),
        sa.CheckConstraint("remote_file_id > 0", name="ck_documents_remote_file_id"),
        sa.CheckConstraint(
            "ingestion_status IN ('NEVER_ATTEMPTED','READY','FAILED')",
            name="ck_documents_ingestion_status",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('NETWORK_TIMEOUT','NETWORK_FAILURE','DOWNLOAD_TOO_LARGE',"
            "'UNAUTHORIZED_REDIRECT','UNAUTHORIZED_ROUTE','INVALID_PDF',"
            "'PDF_ENCRYPTED','PDF_PARSE_FAILED','STORAGE_FAILED','PERSISTENCE_FAILED')",
            name="ck_documents_last_error_code",
        ),
    )
    op.create_index("ix_documents_remote_file_id", "documents", ["remote_file_id"])
    op.create_index("ix_documents_status", "documents", ["ingestion_status"])

    op.create_table(
        "content_blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_content_blobs_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_content_blobs_storage_key"),
        sa.CheckConstraint("id > 0", name="ck_content_blobs_id"),
        sa.CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_content_blobs_sha256",
        ),
        sa.CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 52428800",
            name="ck_content_blobs_size_bytes",
        ),
        sa.CheckConstraint(
            "media_type = 'application/pdf'",
            name="ck_content_blobs_media_type",
        ),
        sa.CheckConstraint(
            "length(storage_key) = 78 "
            "AND substr(storage_key, 1, 7) = 'sha256/' "
            "AND substr(storage_key, 10, 1) = '/' "
            "AND substr(storage_key, 75, 4) = '.pdf' "
            "AND substr(storage_key, 8, 2) NOT GLOB '*[^0-9a-f]*' "
            "AND substr(storage_key, 11, 64) NOT GLOB '*[^0-9a-f]*' "
            "AND substr(storage_key, 8, 2) = substr(storage_key, 11, 2)",
            name="ck_content_blobs_storage_key",
        ),
    )
    op.create_index("ix_content_blobs_sha256", "content_blobs", ["sha256"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "content_blob_id",
            sa.Integer(),
            sa.ForeignKey("content_blobs.id"),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("ingested_at", _utc_datetime(), nullable=False),
        sa.Column("extraction_status", sa.String(32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_version",
        ),
        sa.UniqueConstraint(
            "document_id",
            "content_blob_id",
            name="uq_document_versions_document_blob",
        ),
        sa.CheckConstraint("id > 0", name="ck_document_versions_id"),
        sa.CheckConstraint(
            "document_id > 0",
            name="ck_document_versions_document_id",
        ),
        sa.CheckConstraint(
            "content_blob_id > 0",
            name="ck_document_versions_content_blob_id",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_document_versions_version_number",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('SUCCESS','PARTIAL','NO_TEXT')",
            name="ck_document_versions_extraction_status",
        ),
        sa.CheckConstraint("page_count > 0", name="ck_document_versions_page_count"),
    )
    op.create_index(
        "ix_document_versions_document_version",
        "document_versions",
        ["document_id", "version_number"],
    )

    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_source", sa.String(32), nullable=False),
        sa.Column("width_points", sa.Float(), nullable=False),
        sa.Column("height_points", sa.Float(), nullable=False),
        sa.Column("created_at", _utc_datetime(), nullable=False),
        sa.UniqueConstraint(
            "document_version_id",
            "page_number",
            name="uq_document_pages_version_page",
        ),
        sa.CheckConstraint("id > 0", name="ck_document_pages_id"),
        sa.CheckConstraint(
            "document_version_id > 0",
            name="ck_document_pages_document_version_id",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_document_pages_page_number"),
        sa.CheckConstraint(
            "text_source = 'EMBEDDED_PDF'",
            name="ck_document_pages_text_source",
        ),
        sa.CheckConstraint("width_points > 0", name="ck_document_pages_width_points"),
        sa.CheckConstraint(
            "width_points < 1e999",
            name="ck_document_pages_width_points_finite",
        ),
        sa.CheckConstraint("height_points > 0", name="ck_document_pages_height_points"),
        sa.CheckConstraint(
            "height_points < 1e999",
            name="ck_document_pages_height_points_finite",
        ),
    )
    op.create_index(
        "ix_document_pages_version_page",
        "document_pages",
        ["document_version_id", "page_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_pages_version_page", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_index("ix_document_versions_document_version", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_content_blobs_sha256", table_name="content_blobs")
    op.drop_table("content_blobs")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_remote_file_id", table_name="documents")
    op.drop_table("documents")
