from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aaos.academic.models import CanonicalSnapshot
from aaos.academic.normalization import (
    NormalizationError,
    SourceCollectionError,
    normalize_collection,
)
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
    SourceCollectionFailure,
)


def course(source_id: str = "c1", *, title: str = "Math") -> RawCourse:
    return RawCourse(source_id=source_id, title=title)


def assignment(
    source_id: str = "a1",
    *,
    course_source_id: str = "c1",
    due_at: str | None = None,
    course_source_system: str | None = None,
) -> RawAssignment:
    return RawAssignment(
        source_id=source_id,
        course_source_id=course_source_id,
        title="Assignment",
        due_at=due_at,
        course_source_system=course_source_system,
    )


def complete_snapshot(
    *,
    courses: tuple[RawCourse, ...] = (course(),),
    assignments: tuple[RawAssignment, ...] = (assignment(),),
    announcements: tuple[RawAnnouncement, ...] = (),
    remote_files: tuple[RawRemoteFile, ...] = (),
    source_system: str = "mock:primary",
) -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system=source_system,
        courses=courses,
        assignments=assignments,
        announcements=announcements,
        remote_files=remote_files,
    )


def test_normalization_returns_canonical_snapshot() -> None:
    result = normalize_collection(complete_snapshot())
    assert isinstance(result, CanonicalSnapshot)
    assert result.source_system == "mock:primary"
    assert result.assignments[0].course_source_id == "c1"


def test_duplicate_assignment_identity_is_rejected() -> None:
    source = complete_snapshot(assignments=(assignment("a1"), assignment("a1")))
    with pytest.raises(NormalizationError, match="duplicate"):
        normalize_collection(source)


def test_duplicate_course_identity_is_rejected_even_when_values_match() -> None:
    source = complete_snapshot(courses=(course(), course()))
    with pytest.raises(NormalizationError, match="duplicate"):
        normalize_collection(source)


def test_same_source_id_is_allowed_across_entity_types() -> None:
    source = complete_snapshot(
        assignments=(assignment("same"),),
        announcements=(RawAnnouncement(source_id="same", course_source_id="c1", title="Notice"),),
    )
    result = normalize_collection(source)
    assert result.assignments[0].source_id == result.announcements[0].source_id == "same"


def test_missing_parent_is_rejected() -> None:
    with pytest.raises(NormalizationError, match="parent"):
        normalize_collection(
            complete_snapshot(assignments=(assignment(course_source_id="missing"),))
        )


def test_cross_source_parent_is_rejected() -> None:
    source = complete_snapshot(assignments=(assignment(course_source_system="moodle:other"),))
    with pytest.raises(NormalizationError, match="source namespace"):
        normalize_collection(source)


def test_offset_timestamp_is_aware_utc() -> None:
    result = normalize_collection(
        complete_snapshot(assignments=(assignment(due_at="2026-08-13T15:00:00+03:00"),))
    )
    assert result.assignments[0].due_at == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def test_z_timestamp_is_accepted() -> None:
    result = normalize_collection(
        complete_snapshot(assignments=(assignment(due_at="2026-08-13T12:00:00Z"),))
    )
    assert result.assignments[0].due_at == datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(NormalizationError, match="timezone"):
        normalize_collection(
            complete_snapshot(assignments=(assignment(due_at="2026-08-13T12:00:00"),))
        )


def test_absent_optional_timestamp_remains_none() -> None:
    result = normalize_collection(complete_snapshot(assignments=(assignment(),)))
    assert result.assignments[0].due_at is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/file",
        "https://user:pass@example.test/file",
        "https://example.test/\nfile",
    ],
)
def test_unsafe_remote_url_is_rejected(url: str) -> None:
    source = complete_snapshot(
        remote_files=(
            RawRemoteFile(source_id="r1", course_source_id="c1", title="File", remote_url=url),
        )
    )
    with pytest.raises(NormalizationError, match="URL"):
        normalize_collection(source)


def test_http_url_is_persistable_metadata() -> None:
    source = complete_snapshot(
        remote_files=(
            RawRemoteFile(
                source_id="r1",
                course_source_id="c1",
                title="File",
                remote_url="https://example.test/file",
            ),
        )
    )
    result = normalize_collection(source)
    assert result.remote_files[0].remote_url == "https://example.test/file"


def test_failed_collection_cannot_be_synchronized() -> None:
    with pytest.raises(SourceCollectionError):
        normalize_collection(SourceCollectionFailure(source_system="mock:primary", code="partial"))


def test_complete_empty_snapshot_is_valid() -> None:
    result = normalize_collection(complete_snapshot(courses=(), assignments=()))
    assert result.courses == ()
