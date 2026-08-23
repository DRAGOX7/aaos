"""Server-independent completeness boundary for a Moodle academic source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
    SourceCollection,
    SourceCollectionFailure,
)


@dataclass(frozen=True, slots=True)
class CourseDiscovery:
    """Already-validated course enumeration and its pagination-completeness proof."""

    courses: tuple[RawCourse, ...]
    is_complete: bool


@dataclass(frozen=True, slots=True)
class EntityBundle:
    """Already-validated metadata collected for exactly one discovered course."""

    course_source_id: str
    assignments: tuple[RawAssignment, ...] = ()
    announcements: tuple[RawAnnouncement, ...] = ()
    remote_files: tuple[RawRemoteFile, ...] = ()
    is_complete: bool = False


CourseDiscoveryResult: TypeAlias = CourseDiscovery | SourceCollectionFailure
EntityBundleResult: TypeAlias = EntityBundle | SourceCollectionFailure


class MoodleReadSource(Protocol):
    """Mode-specific reader seam; it exposes records, never transport control."""

    def discover_courses(self) -> CourseDiscoveryResult:
        """Return every course in scope or an application-owned typed failure."""

    def collect_course_entities(self, course: RawCourse) -> EntityBundleResult:
        """Return one complete course bundle or an application-owned typed failure."""


def collect_complete(source: MoodleReadSource, *, source_system: str) -> SourceCollection:
    """Build only a proven-complete Phase 1 source snapshot without persistence."""
    discovery = source.discover_courses()
    if isinstance(discovery, SourceCollectionFailure):
        return _failure(source_system, discovery.code)
    if not isinstance(discovery, CourseDiscovery):
        return _failure(source_system, "malformed_course_discovery")
    if not discovery.is_complete:
        return _failure(source_system, "incomplete_course_discovery")
    if not all(isinstance(course, RawCourse) for course in discovery.courses):
        return _failure(source_system, "malformed_course_discovery")
    if not _unique_course_ids(discovery.courses):
        return _failure(source_system, "duplicate_course_id")

    assignments: list[RawAssignment] = []
    announcements: list[RawAnnouncement] = []
    remote_files: list[RawRemoteFile] = []
    for course in discovery.courses:
        if not isinstance(course, RawCourse):
            return _failure(source_system, "malformed_course_discovery")
        bundle = source.collect_course_entities(course)
        if isinstance(bundle, SourceCollectionFailure):
            return _failure(source_system, bundle.code)
        if not isinstance(bundle, EntityBundle):
            return _failure(source_system, "malformed_entity_bundle")
        if not bundle.is_complete:
            return _failure(source_system, "incomplete_entity_bundle")
        if not _has_valid_record_types(bundle):
            return _failure(source_system, "malformed_entity_bundle")
        if bundle.course_source_id != course.source_id or not _has_valid_parents(
            bundle, course.source_id, source_system
        ):
            return _failure(source_system, "invalid_parent")
        assignments.extend(bundle.assignments)
        announcements.extend(bundle.announcements)
        remote_files.extend(bundle.remote_files)

    return CompleteSourceSnapshot(
        source_system=source_system,
        courses=discovery.courses,
        assignments=tuple(assignments),
        announcements=tuple(announcements),
        remote_files=tuple(remote_files),
    )


def _failure(source_system: str, code: str) -> SourceCollectionFailure:
    return SourceCollectionFailure(source_system=source_system, code=code)


def _unique_course_ids(courses: tuple[RawCourse, ...]) -> bool:
    return len({course.source_id for course in courses}) == len(courses)


def _has_valid_record_types(bundle: EntityBundle) -> bool:
    return (
        all(isinstance(record, RawAssignment) for record in bundle.assignments)
        and all(isinstance(record, RawAnnouncement) for record in bundle.announcements)
        and all(isinstance(record, RawRemoteFile) for record in bundle.remote_files)
    )


def _has_valid_parents(bundle: EntityBundle, course_source_id: str, source_system: str) -> bool:
    children: list[RawAssignment | RawAnnouncement | RawRemoteFile] = [
        *bundle.assignments,
        *bundle.announcements,
        *bundle.remote_files,
    ]
    return all(
        child.course_source_id == course_source_id
        and child.course_source_system in (None, source_system)
        for child in children
    )
