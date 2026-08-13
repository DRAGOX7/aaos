"""Provider-neutral source records and collection outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias


@dataclass(frozen=True, slots=True)
class RawCourse:
    source_id: str
    title: str
    code: str | None = None
    description: str | None = None
    source_updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class RawAssignment:
    source_id: str
    course_source_id: str
    title: str
    description: str | None = None
    due_at: str | None = None
    source_updated_at: str | None = None
    course_source_system: str | None = None


@dataclass(frozen=True, slots=True)
class RawAnnouncement:
    source_id: str
    course_source_id: str
    title: str
    body: str | None = None
    published_at: str | None = None
    source_updated_at: str | None = None
    course_source_system: str | None = None


@dataclass(frozen=True, slots=True)
class RawRemoteFile:
    source_id: str
    course_source_id: str
    title: str
    remote_url: str | None = None
    source_updated_at: str | None = None
    course_source_system: str | None = None


@dataclass(frozen=True, slots=True)
class CompleteSourceSnapshot:
    source_system: str
    courses: tuple[RawCourse, ...] = ()
    assignments: tuple[RawAssignment, ...] = ()
    announcements: tuple[RawAnnouncement, ...] = ()
    remote_files: tuple[RawRemoteFile, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceCollectionFailure:
    source_system: str
    code: str


SourceCollection: TypeAlias = CompleteSourceSnapshot | SourceCollectionFailure


class AcademicSource(Protocol):
    def collect(self, name: str) -> SourceCollection:
        """Return a named complete snapshot or a typed collection failure."""
