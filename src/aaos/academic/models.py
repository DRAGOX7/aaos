"""Immutable canonical academic records exchanged with persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_NORMALIZED_SNAPSHOT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class CanonicalCourse:
    source_system: str
    source_id: str
    title: str
    code: str | None = None
    description: str | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CanonicalAssignment:
    source_system: str
    source_id: str
    course_source_id: str
    title: str
    description: str | None = None
    due_at: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CanonicalAnnouncement:
    source_system: str
    source_id: str
    course_source_id: str
    title: str
    body: str | None = None
    published_at: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CanonicalRemoteFile:
    source_system: str
    source_id: str
    course_source_id: str
    title: str
    remote_url: str | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CanonicalSnapshot:
    source_system: str
    courses: tuple[CanonicalCourse, ...] = ()
    assignments: tuple[CanonicalAssignment, ...] = ()
    announcements: tuple[CanonicalAnnouncement, ...] = ()
    remote_files: tuple[CanonicalRemoteFile, ...] = ()
    _validation_token: object = field(default=None, repr=False, compare=False)
