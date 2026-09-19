"""SQLAlchemy mappings for immutable Phase 8 visual provenance."""

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


class VisualAnalysisRunORM(Base):
    __tablename__ = "visual_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "analysis_key", "attempt_number", name="uq_visual_analysis_runs_key_attempt"
        ),
        CheckConstraint("id > 0", name="ck_visual_analysis_runs_id"),
        CheckConstraint("attempt_number > 0", name="ck_visual_analysis_runs_attempt_number"),
        CheckConstraint(
            "document_version_id > 0", name="ck_visual_analysis_runs_document_version_id"
        ),
        CheckConstraint("page_number > 0", name="ck_visual_analysis_runs_page_number"),
        CheckConstraint(
            "length(analysis_key) = 64 AND analysis_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_analysis_key",
        ),
        CheckConstraint(
            "length(source_content_sha256) = 64 AND source_content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_source_sha256",
        ),
        CheckConstraint(
            "length(render_sha256) = 64 AND render_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_analysis_runs_render_sha256",
        ),
        CheckConstraint(
            "analysis_sha256 IS NULL OR "
            "(length(analysis_sha256) = 64 AND analysis_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_visual_analysis_runs_analysis_sha256",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED','TIMED_OUT','UNAVAILABLE')",
            name="ck_visual_analysis_runs_status",
        ),
        CheckConstraint(
            "((status IN ('SUCCEEDED','PARTIAL') AND analysis_sha256 IS NOT NULL) OR "
            "(status IN ('FAILED','TIMED_OUT','UNAVAILABLE') AND analysis_sha256 IS NULL))",
            name="ck_visual_analysis_runs_status_hash",
        ),
        CheckConstraint(
            "length(model_id) BETWEEN 1 AND 128 AND "
            "length(model_revision) BETWEEN 1 AND 128 AND "
            "length(prompt_revision) BETWEEN 1 AND 128 AND "
            "length(selector_revision) BETWEEN 1 AND 128 AND "
            "length(renderer_revision) BETWEEN 1 AND 128 AND "
            "length(analysis_schema_revision) BETWEEN 1 AND 128",
            name="ck_visual_analysis_runs_revisions",
        ),
        Index(
            "uq_visual_analysis_runs_succeeded_key",
            "analysis_key",
            unique=True,
            sqlite_where=text("status = 'SUCCEEDED'"),
        ),
        Index(
            "ix_visual_analysis_runs_document_page_source",
            "document_version_id",
            "page_number",
            "source_content_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    render_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    selector_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    renderer_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    analysis_schema_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    analysis_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class VisualElementORM(Base):
    __tablename__ = "visual_elements"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "sequence", name="uq_visual_elements_run_sequence"),
        CheckConstraint("id > 0", name="ck_visual_elements_id"),
        CheckConstraint("analysis_run_id > 0", name="ck_visual_elements_analysis_run_id"),
        CheckConstraint("sequence > 0", name="ck_visual_elements_sequence"),
        CheckConstraint(
            "visual_kind IN ('TABLE','EQUATION','CHART','DIAGRAM','TEXT_REGION','OTHER')",
            name="ck_visual_elements_kind",
        ),
        CheckConstraint(
            "observed_text IS NOT NULL OR interpretation_text IS NOT NULL OR "
            "structured_payload IS NOT NULL",
            name="ck_visual_elements_content",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_visual_elements_payload_sha256",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_visual_elements_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("visual_analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpretation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
