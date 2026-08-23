from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine

from aaos.academic.normalization import SourceCollectionError, normalize_collection
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
    SourceCollectionFailure,
)
from aaos.moodle.collection import CourseDiscoveryResult, EntityBundleResult
from aaos.persistence.academic_models import CourseORM
from aaos.persistence.database import create_database_engine, create_session_factory

SOURCE_SYSTEM = "moodle:fixture:complete-scope"


@dataclass(frozen=True, slots=True)
class FixtureReader:
    discovery: CourseDiscoveryResult
    bundles: dict[str, EntityBundleResult]

    def discover_courses(self) -> CourseDiscoveryResult:
        return self.discovery

    def collect_course_entities(self, course: RawCourse) -> EntityBundleResult:
        return self.bundles[course.source_id]


def course(source_id: str = "course-1") -> RawCourse:
    return RawCourse(source_id=source_id, title="Synthetic course")


def test_complete_fixture_collection_returns_all_four_entity_types() -> None:
    from aaos.moodle.collection import CourseDiscovery, EntityBundle, collect_complete

    source = FixtureReader(
        discovery=CourseDiscovery(courses=(course(),), is_complete=True),
        bundles={
            "course-1": EntityBundle(
                course_source_id="course-1",
                assignments=(
                    RawAssignment(
                        source_id="assignment-1",
                        course_source_id="course-1",
                        title="Synthetic assignment",
                    ),
                ),
                announcements=(
                    RawAnnouncement(
                        source_id="announcement-1",
                        course_source_id="course-1",
                        title="Synthetic announcement",
                    ),
                ),
                remote_files=(
                    RawRemoteFile(
                        source_id="file-1",
                        course_source_id="course-1",
                        title="Synthetic remote-file metadata",
                        remote_url="https://moodle.example.test/pluginfile.php/1/file.pdf",
                    ),
                ),
                is_complete=True,
            ),
        },
    )

    result = collect_complete(source, source_system=SOURCE_SYSTEM)

    assert isinstance(result, CompleteSourceSnapshot)
    assert result.source_system == SOURCE_SYSTEM
    assert tuple(record.source_id for record in result.courses) == ("course-1",)
    assert tuple(record.source_id for record in result.assignments) == ("assignment-1",)
    assert tuple(record.source_id for record in result.announcements) == ("announcement-1",)
    assert tuple(record.source_id for record in result.remote_files) == ("file-1",)


def test_incomplete_course_discovery_returns_typed_failure() -> None:
    from aaos.moodle.collection import CourseDiscovery, collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=CourseDiscovery(courses=(course(),), is_complete=False), bundles={}
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(
        source_system=SOURCE_SYSTEM,
        code="incomplete_course_discovery",
    )


def test_pagination_failure_is_returned_without_synthesizing_an_empty_snapshot() -> None:
    from aaos.moodle.collection import collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=SourceCollectionFailure(SOURCE_SYSTEM, "pagination_failed"),
            bundles={},
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(source_system=SOURCE_SYSTEM, code="pagination_failed")


def test_incomplete_child_bundle_returns_typed_failure() -> None:
    from aaos.moodle.collection import CourseDiscovery, EntityBundle, collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=CourseDiscovery(courses=(course(),), is_complete=True),
            bundles={"course-1": EntityBundle(course_source_id="course-1", is_complete=False)},
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(
        source_system=SOURCE_SYSTEM,
        code="incomplete_entity_bundle",
    )


def test_child_with_a_missing_parent_is_rejected_before_normalization() -> None:
    from aaos.moodle.collection import CourseDiscovery, EntityBundle, collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=CourseDiscovery(courses=(course(),), is_complete=True),
            bundles={
                "course-1": EntityBundle(
                    course_source_id="course-1",
                    assignments=(
                        RawAssignment(
                            source_id="assignment-1",
                            course_source_id="missing-course",
                            title="Synthetic assignment",
                        ),
                    ),
                    is_complete=True,
                ),
            },
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(source_system=SOURCE_SYSTEM, code="invalid_parent")


def test_malformed_child_record_returns_a_typed_failure() -> None:
    from aaos.moodle.collection import CourseDiscovery, EntityBundle, collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=CourseDiscovery(courses=(course(),), is_complete=True),
            bundles={
                "course-1": EntityBundle(
                    course_source_id="course-1",
                    assignments=("not-a-record",),  # type: ignore[arg-type]
                    is_complete=True,
                ),
            },
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(
        source_system=SOURCE_SYSTEM,
        code="malformed_entity_bundle",
    )


def test_duplicate_course_identity_is_rejected_before_normalization() -> None:
    from aaos.moodle.collection import CourseDiscovery, collect_complete

    result = collect_complete(
        FixtureReader(
            discovery=CourseDiscovery(courses=(course(), course()), is_complete=True),
            bundles={},
        ),
        source_system=SOURCE_SYSTEM,
    )

    assert result == SourceCollectionFailure(
        source_system=SOURCE_SYSTEM, code="duplicate_course_id"
    )


def test_collection_failure_cannot_reach_the_phase_1_database_sync_boundary(tmp_path: Path) -> None:
    from aaos.moodle.collection import collect_complete

    engine: Engine = create_database_engine(tmp_path / "collection-contract.db")
    CourseORM.metadata.create_all(engine)
    try:
        result = collect_complete(
            FixtureReader(
                discovery=SourceCollectionFailure(SOURCE_SYSTEM, "malformed_response"),
                bundles={},
            ),
            source_system=SOURCE_SYSTEM,
        )

        assert isinstance(result, SourceCollectionFailure)
        with pytest.raises(SourceCollectionError):
            normalize_collection(result)
        with create_session_factory(engine)() as session:
            assert session.query(CourseORM).count() == 0
    finally:
        engine.dispose()
