"""SQLAlchemy mappings for the Phase 4 local-RAG persistence schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from aaos.persistence.academic_models import Base, UTCDateTime


class RagIndexGenerationORM(Base):
    """Immutable retrieval configuration plus mutable generation lifecycle state."""

    __tablename__ = "rag_index_generations"
    __table_args__ = (
        CheckConstraint("id > 0", name="ck_rag_index_generations_id"),
        CheckConstraint(
            "status IN ('BUILDING','ACTIVE','RETIRED')",
            name="ck_rag_index_generations_status",
        ),
        CheckConstraint(
            "length(config_fingerprint) = 64 "
            "AND config_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_index_generations_config_fingerprint",
        ),
        CheckConstraint(
            "embedding_dimensions > 0",
            name="ck_rag_index_generations_embedding_dimensions",
        ),
        CheckConstraint(
            "content_revision >= 0",
            name="ck_rag_index_generations_content_revision",
        ),
        Index(
            "uq_rag_index_generations_one_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_config: Mapped[str] = mapped_column(Text, nullable=False)
    lexical_normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_config: Mapped[str] = mapped_column(Text, nullable=False)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class RagDocumentIndexORM(Base):
    """The indexed state and coverage of one document version in a generation."""

    __tablename__ = "rag_document_indexes"
    __table_args__ = (
        UniqueConstraint(
            "generation_id",
            "document_version_id",
            name="uq_rag_document_indexes_generation_document",
        ),
        CheckConstraint("id > 0", name="ck_rag_document_indexes_id"),
        CheckConstraint(
            "generation_id > 0", name="ck_rag_document_indexes_generation_id"
        ),
        CheckConstraint(
            "document_version_id > 0", name="ck_rag_document_indexes_document_version_id"
        ),
        CheckConstraint(
            "status IN ('PREPARING','READY','NOT_SEARCHABLE','FAILED')",
            name="ck_rag_document_indexes_status",
        ),
        CheckConstraint(
            "coverage_status IN ('FULL','PARTIAL','NONE')",
            name="ck_rag_document_indexes_coverage_status",
        ),
        CheckConstraint(
            "searchable_page_count >= 0",
            name="ck_rag_document_indexes_searchable_page_count",
        ),
        CheckConstraint(
            "chunk_count >= 0", name="ck_rag_document_indexes_chunk_count"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("rag_index_generations.id"), nullable=False
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    coverage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    searchable_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RagChunkORM(Base):
    """Authoritative derived chunk text, identity, and compact page provenance."""

    __tablename__ = "rag_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_key", name="uq_rag_chunks_chunk_key"),
        UniqueConstraint(
            "generation_id",
            "document_version_id",
            "ordinal",
            name="uq_rag_chunks_generation_document_ordinal",
        ),
        CheckConstraint("id > 0", name="ck_rag_chunks_id"),
        CheckConstraint("generation_id > 0", name="ck_rag_chunks_generation_id"),
        CheckConstraint(
            "document_version_id > 0", name="ck_rag_chunks_document_version_id"
        ),
        CheckConstraint(
            "length(chunk_key) = 64 AND chunk_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_chunks_chunk_key",
        ),
        CheckConstraint("ordinal > 0", name="ck_rag_chunks_ordinal"),
        CheckConstraint("length(exact_text) > 0", name="ck_rag_chunks_exact_text"),
        CheckConstraint("token_count > 0", name="ck_rag_chunks_token_count"),
        CheckConstraint(
            "length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_rag_chunks_text_sha256",
        ),
        CheckConstraint(
            "page_start > 0 AND page_end >= page_start", name="ck_rag_chunks_page_bounds"
        ),
        Index("ix_rag_chunks_generation_document_ordinal", "generation_id", "document_version_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_key: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("rag_index_generations.id"), nullable=False
    )
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    exact_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RagChunkSourceSpanORM(Base):
    """An ordered, exact character-range contribution from a Phase 3 page."""

    __tablename__ = "rag_chunk_source_spans"
    __table_args__ = (
        UniqueConstraint(
            "rag_chunk_id", "ordinal", name="uq_rag_chunk_source_spans_chunk_ordinal"
        ),
        CheckConstraint("id > 0", name="ck_rag_chunk_source_spans_id"),
        CheckConstraint("rag_chunk_id > 0", name="ck_rag_chunk_source_spans_chunk_id"),
        CheckConstraint(
            "document_page_id > 0", name="ck_rag_chunk_source_spans_document_page_id"
        ),
        CheckConstraint("ordinal > 0", name="ck_rag_chunk_source_spans_ordinal"),
        CheckConstraint("page_number > 0", name="ck_rag_chunk_source_spans_page_number"),
        CheckConstraint(
            "page_char_start >= 0 AND page_char_end > page_char_start",
            name="ck_rag_chunk_source_spans_page_bounds",
        ),
        CheckConstraint(
            "chunk_char_start >= 0 AND chunk_char_end > chunk_char_start",
            name="ck_rag_chunk_source_spans_chunk_bounds",
        ),
        Index("ix_rag_chunk_source_spans_chunk_ordinal", "rag_chunk_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rag_chunk_id: Mapped[int] = mapped_column(ForeignKey("rag_chunks.id"), nullable=False)
    document_page_id: Mapped[int] = mapped_column(
        ForeignKey("document_pages.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
