"""SQLAlchemy mappings for the Phase 3 document-ingestion schema."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from aaos.documents.contracts import ExtractionStatus, IngestionErrorCode, IngestionStatus
from aaos.persistence.academic_models import Base, UTCDateTime


def _sql_enum_values(enum_type: type[StrEnum]) -> str:
    return ",".join(f"'{member.value}'" for member in enum_type)


class DocumentORM(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("remote_file_id", name="uq_documents_remote_file_id"),
        CheckConstraint("id > 0", name="ck_documents_id"),
        CheckConstraint("remote_file_id > 0", name="ck_documents_remote_file_id"),
        CheckConstraint(
            f"ingestion_status IN ({_sql_enum_values(IngestionStatus)})",
            name="ck_documents_ingestion_status",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR "
            f"last_error_code IN ({_sql_enum_values(IngestionErrorCode)})",
            name="ck_documents_last_error_code",
        ),
        Index("ix_documents_remote_file_id", "remote_file_id"),
        Index("ix_documents_status", "ingestion_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_file_id: Mapped[int] = mapped_column(ForeignKey("remote_files.id"), nullable=False)
    ingestion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ContentBlobORM(Base):
    __tablename__ = "content_blobs"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_content_blobs_sha256"),
        UniqueConstraint("storage_key", name="uq_content_blobs_storage_key"),
        CheckConstraint("id > 0", name="ck_content_blobs_id"),
        CheckConstraint(
            "length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_content_blobs_sha256",
        ),
        CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 52428800",
            name="ck_content_blobs_size_bytes",
        ),
        CheckConstraint(
            "media_type = 'application/pdf'",
            name="ck_content_blobs_media_type",
        ),
        CheckConstraint(
            "length(storage_key) = 78 "
            "AND substr(storage_key, 1, 7) = 'sha256/' "
            "AND substr(storage_key, 10, 1) = '/' "
            "AND substr(storage_key, 75, 4) = '.pdf' "
            "AND substr(storage_key, 8, 2) NOT GLOB '*[^0-9a-f]*' "
            "AND substr(storage_key, 11, 64) NOT GLOB '*[^0-9a-f]*' "
            "AND substr(storage_key, 8, 2) = substr(storage_key, 11, 2)",
            name="ck_content_blobs_storage_key",
        ),
        Index("ix_content_blobs_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DocumentVersionORM(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_version",
        ),
        UniqueConstraint(
            "document_id",
            "content_blob_id",
            name="uq_document_versions_document_blob",
        ),
        CheckConstraint("id > 0", name="ck_document_versions_id"),
        CheckConstraint("document_id > 0", name="ck_document_versions_document_id"),
        CheckConstraint(
            "content_blob_id > 0",
            name="ck_document_versions_content_blob_id",
        ),
        CheckConstraint("version_number > 0", name="ck_document_versions_version_number"),
        CheckConstraint(
            f"extraction_status IN ({_sql_enum_values(ExtractionStatus)})",
            name="ck_document_versions_extraction_status",
        ),
        CheckConstraint("page_count > 0", name="ck_document_versions_page_count"),
        Index("ix_document_versions_document_version", "document_id", "version_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_blob_id: Mapped[int] = mapped_column(ForeignKey("content_blobs.id"), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DocumentPageORM(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "page_number",
            name="uq_document_pages_version_page",
        ),
        CheckConstraint("id > 0", name="ck_document_pages_id"),
        CheckConstraint(
            "document_version_id > 0",
            name="ck_document_pages_document_version_id",
        ),
        CheckConstraint("page_number > 0", name="ck_document_pages_page_number"),
        CheckConstraint(
            "text_source = 'EMBEDDED_PDF'",
            name="ck_document_pages_text_source",
        ),
        CheckConstraint("width_points > 0", name="ck_document_pages_width_points"),
        CheckConstraint(
            "width_points < 1e999",
            name="ck_document_pages_width_points_finite",
        ),
        CheckConstraint("height_points > 0", name="ck_document_pages_height_points"),
        CheckConstraint(
            "height_points < 1e999",
            name="ck_document_pages_height_points_finite",
        ),
        Index("ix_document_pages_version_page", "document_version_id", "page_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_source: Mapped[str] = mapped_column(String(32), nullable=False)
    width_points: Mapped[float] = mapped_column(Float, nullable=False)
    height_points: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
