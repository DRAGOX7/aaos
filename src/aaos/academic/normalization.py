"""Pure validation and normalization for academic source snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from aaos.academic.models import (
    _NORMALIZED_SNAPSHOT_TOKEN,
    CanonicalAnnouncement,
    CanonicalAssignment,
    CanonicalCourse,
    CanonicalRemoteFile,
    CanonicalSnapshot,
)
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawCourse,
    SourceCollection,
    SourceCollectionFailure,
)


class NormalizationError(ValueError):
    """Raised when source data violates a canonical contract."""


class SourceCollectionError(NormalizationError):
    """Raised when a source did not produce a complete enumeration."""


def _text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(f"{label} must be text")
    normalized = value.strip()
    if not normalized or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise NormalizationError(f"{label} must be non-blank safe text")
    return normalized


def _namespace(value: str) -> str:
    normalized = _text(value, "source namespace").lower()
    if any(char.isspace() for char in normalized):
        raise NormalizationError("source namespace must not contain whitespace")
    return normalized


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _timestamp(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NormalizationError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalizationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NormalizationError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _remote_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise NormalizationError("remote URL contains unsafe control characters")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError as exc:
        raise NormalizationError("remote URL must be structurally valid") from exc
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        raise NormalizationError(
            "remote URL must be a structurally valid HTTP(S) URL without userinfo"
        )
    if parts.hostname is None:
        raise NormalizationError("remote URL must include a host")
    return value


def _unique_ids(records: tuple[object, ...], label: str, source_system: str) -> None:
    seen: set[str] = set()
    for record in records:
        source_id = _text(getattr(record, "source_id"), f"{label} source_id")
        if source_id in seen:
            raise NormalizationError(
                f"duplicate {label} identity in source namespace {source_system}"
            )
        seen.add(source_id)


def _check_parent(record: object, source_system: str, course_ids: set[str]) -> str:
    parent_namespace = getattr(record, "course_source_system")
    if parent_namespace is not None and _namespace(parent_namespace) != source_system:
        raise NormalizationError("parent source namespace does not match snapshot")
    parent_id = _text(getattr(record, "course_source_id"), "course source_id")
    if parent_id not in course_ids:
        raise NormalizationError("child parent course does not exist in source namespace")
    return parent_id


def _course(record: RawCourse, source_system: str) -> CanonicalCourse:
    return CanonicalCourse(
        source_system=source_system,
        source_id=_text(record.source_id, "course source_id"),
        title=_text(record.title, "course title"),
        code=_optional_text(record.code, "course code"),
        description=_optional_text(record.description, "course description"),
        source_updated_at=_timestamp(record.source_updated_at, "course updated_at"),
    )


def normalize_collection(collection: SourceCollection) -> CanonicalSnapshot:
    """Validate a complete collection without I/O or database writes."""
    if isinstance(collection, SourceCollectionFailure):
        raise SourceCollectionError(f"source collection failed: {collection.code}")
    if not isinstance(collection, CompleteSourceSnapshot):
        raise SourceCollectionError("source collection is not complete")

    source_system = _namespace(collection.source_system)
    _unique_ids(collection.courses, "course", source_system)
    _unique_ids(collection.assignments, "assignment", source_system)
    _unique_ids(collection.announcements, "announcement", source_system)
    _unique_ids(collection.remote_files, "remote_file", source_system)
    courses = tuple(_course(record, source_system) for record in collection.courses)
    course_ids = {record.source_id for record in courses}

    assignments = tuple(
        CanonicalAssignment(
            source_system=source_system,
            source_id=_text(record.source_id, "assignment source_id"),
            course_source_id=_check_parent(record, source_system, course_ids),
            title=_text(record.title, "assignment title"),
            description=_optional_text(record.description, "assignment description"),
            due_at=_timestamp(record.due_at, "assignment due_at"),
            source_updated_at=_timestamp(record.source_updated_at, "assignment updated_at"),
        )
        for record in collection.assignments
    )
    announcements = tuple(
        CanonicalAnnouncement(
            source_system=source_system,
            source_id=_text(record.source_id, "announcement source_id"),
            course_source_id=_check_parent(record, source_system, course_ids),
            title=_text(record.title, "announcement title"),
            body=_optional_text(record.body, "announcement body"),
            published_at=_timestamp(record.published_at, "announcement published_at"),
            source_updated_at=_timestamp(record.source_updated_at, "announcement updated_at"),
        )
        for record in collection.announcements
    )
    remote_files = tuple(
        CanonicalRemoteFile(
            source_system=source_system,
            source_id=_text(record.source_id, "remote_file source_id"),
            course_source_id=_check_parent(record, source_system, course_ids),
            title=_text(record.title, "remote_file title"),
            remote_url=_remote_url(record.remote_url),
            source_updated_at=_timestamp(record.source_updated_at, "remote_file updated_at"),
        )
        for record in collection.remote_files
    )
    return CanonicalSnapshot(
        source_system=source_system,
        courses=courses,
        assignments=assignments,
        announcements=announcements,
        remote_files=remote_files,
        _validation_token=_NORMALIZED_SNAPSHOT_TOKEN,
    )
