"""SQLAlchemy mappings for the Phase 1 academic domain."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Index, Integer, MetaData, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware UTC datetimes as canonical ISO-8601 ``Z`` text."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include a timezone")
        normalized = value.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")

    def process_result_value(self, value: str | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("stored datetime must include a timezone")
        return parsed.astimezone(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData()


class CourseORM(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("source_system", "source_id", name="uq_courses_source_identity"),
        Index("ix_courses_source_active", "source_system", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AssignmentORM(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("source_system", "source_id", name="uq_assignments_source_identity"),
        Index("ix_assignments_course_active", "course_id", "is_active"),
        Index("ix_assignments_source_active", "source_system", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AnnouncementORM(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("source_system", "source_id", name="uq_announcements_source_identity"),
        Index("ix_announcements_course_active", "course_id", "is_active"),
        Index("ix_announcements_source_active", "source_system", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(String(8000), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class RemoteFileORM(Base):
    __tablename__ = "remote_files"
    __table_args__ = (
        UniqueConstraint("source_system", "source_id", name="uq_remote_files_source_identity"),
        Index("ix_remote_files_course_active", "course_id", "is_active"),
        Index("ix_remote_files_source_active", "source_system", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    remote_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
