from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
    SourceCollectionFailure,
)
from aaos.academic.sync import SynchronizationConflict
from aaos.documents.contracts import ExtractionStatus, IngestionStatus
from aaos.moodle.change_read import MoodleChangeReadService
from aaos.moodle.changes import (
    ChangeField,
    ChangeKind,
    ComparisonStatus,
    ComparisonUnavailableReason,
    NormalizedMoodleState,
)
from aaos.moodle.integration import (
    MoodleSyncPreconditionError,
    build_isolated_database,
    sync_moodle_snapshot,
)
from aaos.moodle.sync_history import (
    MoodleSyncAlreadyRunning,
    MoodleSyncFailureCode,
    MoodleSyncHistoryError,
    MoodleSyncHistoryRepository,
)
from aaos.persistence.academic_models import (
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentVersionORM,
)
from aaos.persistence.moodle_sync_models import MoodleSyncSnapshotORM

SOURCE = "moodle:fixture:changes"
UTC = timezone.utc


def build_snapshot(
    *,
    assignment_due: str = "2026-10-01T09:00:00Z",
    documents: tuple[RawRemoteFile, ...] = (),
    source_system: str = SOURCE,
) -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system=source_system,
        courses=(RawCourse("course-1", "Deep Learning", "CS401"),),
        assignments=(
            RawAssignment(
                "assignment-1",
                "course-1",
                "Assignment 1",
                due_at=assignment_due,
            ),
        ),
        remote_files=documents,
    )


def sync_at(
    factory: sessionmaker[Session],
    collection: CompleteSourceSnapshot,
    when: datetime,
) -> None:
    sync_moodle_snapshot(
        collection,
        session_factory=factory,
        started_at=when,
        finished_at=when,
    )


def service_for(path: Path) -> tuple[Engine, sessionmaker[Session]]:
    engine, factory = build_isolated_database(path)
    return engine, factory


def add_document_version(
    factory: sessionmaker[Session],
    *,
    remote_file_id: int,
    version_number: int,
    ingested_at: datetime,
) -> None:
    session = factory()
    digest = f"{version_number:064x}"
    try:
        with session.begin():
            document = (
                session.query(DocumentORM).filter_by(remote_file_id=remote_file_id).one_or_none()
            )
            if document is None:
                document = DocumentORM(
                    remote_file_id=remote_file_id,
                    ingestion_status=IngestionStatus.READY.value,
                    last_attempted_at=ingested_at,
                    last_error_code=None,
                    created_at=ingested_at,
                    updated_at=ingested_at,
                )
                session.add(document)
                session.flush()
            blob = ContentBlobORM(
                sha256=digest,
                size_bytes=4,
                media_type="application/pdf",
                storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
                created_at=ingested_at,
            )
            session.add(blob)
            session.flush()
            session.add(
                DocumentVersionORM(
                    document_id=document.id,
                    version_number=version_number,
                    content_blob_id=blob.id,
                    source_filename="source.pdf",
                    ingested_at=ingested_at,
                    extraction_status=ExtractionStatus.SUCCESS.value,
                    provenance_mode="PAGE",
                    page_count=1,
                )
            )
    finally:
        session.close()


def test_disposable_migrations_preserve_snapshot_history_and_change_linkage(
    tmp_path: Path,
) -> None:
    engine, _factory = service_for(tmp_path / "phase-e.db")
    try:
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == (
                "0014_archived_courses"
            )
        assert "moodle_sync_snapshots" in set(inspect(engine).get_table_names())
        assert {
            "uq_moodle_sync_snapshots_one_running",
            "ix_moodle_sync_snapshots_source_finished",
        } <= {index["name"] for index in inspect(engine).get_indexes("moodle_sync_snapshots")}
        assert {
            "source_change_id",
            "source_from_snapshot_id",
            "source_to_snapshot_id",
            "source_changed_field",
        } <= {column["name"] for column in inspect(engine).get_columns("study_task_mutations")}
    finally:
        engine.dispose()


def test_latest_sync_diff_and_since_query_use_net_snapshot_state(tmp_path: Path) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    first_at = now - timedelta(days=3)
    middle_at = now - timedelta(days=2)
    latest_at = now - timedelta(days=1)
    file_a = RawRemoteFile("resource-cm:91", "course-1", "Lecture.pdf")
    file_b = RawRemoteFile("resource-cm:92", "course-1", "Temporary.pdf")
    try:
        sync_at(factory, build_snapshot(documents=(file_a,)), first_at)
        sync_at(
            factory,
            build_snapshot(assignment_due="2026-10-02T09:00:00Z", documents=(file_a, file_b)),
            middle_at,
        )
        sync_at(
            factory,
            build_snapshot(assignment_due="2026-10-03T09:00:00Z", documents=(file_a,)),
            latest_at,
        )
        reader = MoodleChangeReadService(factory)

        latest = reader.list_changes(query_time=now)
        since_first = reader.list_changes(
            since=first_at + timedelta(hours=1),
            query_time=now,
        )
        since_current = reader.list_changes(since=latest_at, query_time=now)

        assert latest.comparison_status is ComparisonStatus.AVAILABLE
        assert latest.from_snapshot_id is not None and latest.to_snapshot_id is not None
        assert [(item.entity_type.value, item.change_kind) for item in latest.records] == [
            ("ASSIGNMENT", ChangeKind.MODIFIED),
            ("DOCUMENT", ChangeKind.REMOVED),
        ]
        assert latest.records[0].before_due_at == datetime(2026, 10, 2, 9, tzinfo=UTC)
        assert latest.records[0].after_due_at == datetime(2026, 10, 3, 9, tzinfo=UTC)
        assert since_first.comparison_status is ComparisonStatus.AVAILABLE
        assert len(since_first.records) == 1
        assert since_first.records[0].before_due_at == datetime(2026, 10, 1, 9, tzinfo=UTC)
        assert since_first.records[0].after_due_at == datetime(2026, 10, 3, 9, tzinfo=UTC)
        assert since_current.comparison_status is ComparisonStatus.AVAILABLE
        assert since_current.records == ()
        assert latest.records[0].course_id is not None and latest.records[0].entity_id is not None
        assert latest.records[0].course_title == "Deep Learning"
    finally:
        engine.dispose()


def test_change_service_returns_typed_unavailable_without_two_comparable_states(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    try:
        reader = MoodleChangeReadService(factory)
        empty = reader.list_changes(query_time=now)
        assert empty.comparison_status is ComparisonStatus.UNAVAILABLE
        assert empty.unavailable_reason is ComparisonUnavailableReason.NO_BASELINE

        sync_at(factory, build_snapshot(), now - timedelta(days=1))
        one_snapshot = reader.list_changes(query_time=now)
        assert one_snapshot.comparison_status is ComparisonStatus.UNAVAILABLE
        assert one_snapshot.unavailable_reason is ComparisonUnavailableReason.NO_BASELINE
    finally:
        engine.dispose()


def test_incomplete_latest_attempt_blocks_removals_instead_of_using_stale_state(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    try:
        sync_at(factory, build_snapshot(), now - timedelta(days=2))
        sync_at(
            factory, build_snapshot(assignment_due="2026-10-02T09:00:00Z"), now - timedelta(days=1)
        )
        history = MoodleSyncHistoryRepository(factory)
        attempt_id = history.begin(SOURCE, started_at=now)
        with pytest.raises(MoodleSyncPreconditionError):
            sync_moodle_snapshot(
                SourceCollectionFailure(SOURCE, "incomplete_page"),
                session_factory=factory,
                sync_attempt_id=attempt_id,
                finished_at=now + timedelta(seconds=1),
            )

        result = MoodleChangeReadService(factory).list_changes(
            query_time=now + timedelta(minutes=1)
        )

        assert result.comparison_status is ComparisonStatus.UNAVAILABLE
        assert result.unavailable_reason is ComparisonUnavailableReason.LATEST_SYNC_INCOMPLETE
        with factory() as session:
            latest = (
                session.query(MoodleSyncSnapshotORM)
                .order_by(MoodleSyncSnapshotORM.id.desc())
                .first()
            )
            assert latest is not None
            assert latest.status == "FAILED"
            assert latest.failure_code == MoodleSyncFailureCode.COLLECTION_INCOMPLETE.value
            assert session.query(AssignmentORM).one().is_active is True
    finally:
        engine.dispose()


def test_failed_attempt_before_explicit_boundary_makes_that_comparison_unavailable(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    boundary = now - timedelta(days=1)
    try:
        sync_at(factory, build_snapshot(), now - timedelta(days=3))
        history = MoodleSyncHistoryRepository(factory)
        failed_id = history.begin(SOURCE, started_at=boundary - timedelta(hours=2))
        history.fail(
            failed_id,
            MoodleSyncFailureCode.COLLECTION_INCOMPLETE,
            finished_at=boundary - timedelta(hours=1),
        )
        sync_at(
            factory,
            build_snapshot(assignment_due="2026-10-03T09:00:00Z"),
            now - timedelta(hours=1),
        )

        result = MoodleChangeReadService(factory).list_changes(since=boundary, query_time=now)

        assert result.comparison_status is ComparisonStatus.UNAVAILABLE
        assert result.unavailable_reason is ComparisonUnavailableReason.LATEST_SYNC_INCOMPLETE
    finally:
        engine.dispose()


def test_sync_failure_rolls_back_academic_rows_and_marks_history_failed(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    try:
        sync_at(factory, build_snapshot(), now - timedelta(days=1))
        history = MoodleSyncHistoryRepository(factory)
        attempt_id = history.begin(SOURCE, started_at=now)
        conflicting = CompleteSourceSnapshot(
            source_system=SOURCE,
            courses=(
                RawCourse("course-1", "Deep Learning", "CS401"),
                RawCourse("course-2", "Other Course", "CS402"),
            ),
            assignments=(RawAssignment("assignment-1", "course-2", "Moved assignment"),),
        )

        with pytest.raises(SynchronizationConflict):
            sync_moodle_snapshot(
                conflicting,
                session_factory=factory,
                sync_attempt_id=attempt_id,
                finished_at=now + timedelta(seconds=1),
            )

        with factory() as session:
            assignment = session.query(AssignmentORM).one()
            course = session.query(CourseORM).one()
            attempt = session.get(MoodleSyncSnapshotORM, attempt_id)
            assert assignment.course_id == course.id
            assert assignment.title == "Assignment 1"
            assert session.query(CourseORM).count() == 1
            assert attempt is not None
            assert attempt.status == "FAILED"
            assert attempt.failure_code == MoodleSyncFailureCode.SYNCHRONIZATION_FAILED.value

        result = MoodleChangeReadService(factory).list_changes(
            query_time=now + timedelta(minutes=1)
        )
        assert result.comparison_status is ComparisonStatus.UNAVAILABLE
        assert result.unavailable_reason is ComparisonUnavailableReason.LATEST_SYNC_INCOMPLETE
    finally:
        engine.dispose()


def test_only_one_running_attempt_per_source_is_allowed(tmp_path: Path) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    history = MoodleSyncHistoryRepository(factory)
    try:
        now = datetime.now(UTC)
        attempt_id = history.begin(SOURCE, started_at=now)
        with pytest.raises(MoodleSyncAlreadyRunning):
            history.begin(SOURCE, started_at=now + timedelta(seconds=1))
        history.fail(
            attempt_id,
            MoodleSyncFailureCode.COLLECTION_INCOMPLETE,
            finished_at=now + timedelta(seconds=2),
        )
        next_attempt_id = history.begin(SOURCE, started_at=now + timedelta(seconds=3))
        history.fail(
            next_attempt_id,
            MoodleSyncFailureCode.COLLECTION_INCOMPLETE,
            finished_at=now + timedelta(seconds=4),
        )
    finally:
        engine.dispose()


def test_hand_built_normalized_state_cannot_complete_sync_history(tmp_path: Path) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    history = MoodleSyncHistoryRepository(factory)
    now = datetime.now(UTC)
    try:
        attempt_id = history.begin(SOURCE, started_at=now)
        state = NormalizedMoodleState(SOURCE, (), (), (), ())
        session = factory()
        try:
            with session.begin(), pytest.raises(MoodleSyncHistoryError):
                history.complete_in_session(
                    session,
                    attempt_id,
                    state,
                    finished_at=now + timedelta(seconds=1),
                )
        finally:
            session.close()

        with factory() as session:
            attempt = session.get(MoodleSyncSnapshotORM, attempt_id)
            assert attempt is not None
            assert attempt.status == "RUNNING"
            assert attempt.snapshot_state is None
    finally:
        engine.dispose()


def test_course_scope_and_pages_are_bounded_and_deterministic(tmp_path: Path) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    try:
        sync_at(factory, build_snapshot(), now - timedelta(days=1))
        additions = tuple(
            RawRemoteFile(f"resource-cm:{index}", "course-1", f"Lecture {index}.pdf")
            for index in range(1, 18)
        )
        sync_at(factory, build_snapshot(documents=additions), now)
        with factory() as session:
            course_id = session.query(CourseORM).one().id
        reader = MoodleChangeReadService(factory)

        first_page = reader.list_changes(course_id=course_id, query_time=now + timedelta(minutes=1))
        second_page = reader.list_changes(
            course_id=course_id,
            offset=15,
            query_time=now + timedelta(minutes=1),
        )

        assert len(first_page.records) == 15
        assert first_page.has_more is True
        assert first_page.total_change_count == 17
        assert len(second_page.records) == 2
        assert second_page.has_more is False
        assert [item.change_id for item in (*first_page.records, *second_page.records)] == [
            f"{first_page.from_snapshot_id}-{first_page.to_snapshot_id}-{index}"
            for index in range(1, 18)
        ]
    finally:
        engine.dispose()


def test_global_query_refuses_to_mix_multiple_moodle_source_namespaces(tmp_path: Path) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    try:
        sync_at(factory, build_snapshot(source_system="moodle:one"), now - timedelta(days=1))
        sync_at(factory, build_snapshot(source_system="moodle:two"), now)
        result = MoodleChangeReadService(factory).list_changes(
            query_time=now + timedelta(minutes=1)
        )

        assert result.comparison_status is ComparisonStatus.UNAVAILABLE
        assert result.unavailable_reason is ComparisonUnavailableReason.SOURCE_AMBIGUOUS
    finally:
        engine.dispose()


def test_document_version_history_uses_immutable_versions_by_provider_identity(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    first_at = now - timedelta(days=2)
    latest_at = now - timedelta(days=1)
    try:
        resource = RawRemoteFile("resource-cm:91", "course-1", "Lecture.pdf")
        sync_at(factory, build_snapshot(documents=(resource,)), first_at)
        with factory() as session:
            remote_file_id = session.query(RemoteFileORM).one().id
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=1,
            ingested_at=first_at - timedelta(hours=1),
        )
        sync_at(factory, build_snapshot(documents=(resource,)), latest_at)
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=2,
            ingested_at=first_at + timedelta(hours=1),
        )

        result = MoodleChangeReadService(factory).list_changes(query_time=now)

        assert result.comparison_status is ComparisonStatus.AVAILABLE
        assert len(result.records) == 1
        change = result.records[0]
        assert change.entity_type.value == "DOCUMENT"
        assert change.change_kind is ChangeKind.MODIFIED
        assert change.changed_fields == (ChangeField.DOCUMENT_VERSION,)
        assert (change.before_document_version, change.after_document_version) == (1, 2)
    finally:
        engine.dispose()


def test_latest_sync_comparison_ignores_versions_ingested_after_its_endpoint(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    previous_finished_at = now - timedelta(days=3)
    current_finished_at = now - timedelta(days=2)
    first_version_at = previous_finished_at - timedelta(hours=1)
    post_sync_version_at = current_finished_at + timedelta(hours=1)
    try:
        resource = RawRemoteFile("resource-cm:91", "course-1", "Lecture.pdf")
        collection = build_snapshot(documents=(resource,))
        sync_at(factory, collection, previous_finished_at)
        sync_at(factory, collection, current_finished_at)
        with factory() as session:
            remote_file_id = session.query(RemoteFileORM).one().id
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=1,
            ingested_at=first_version_at,
        )
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=2,
            ingested_at=post_sync_version_at,
        )

        result = MoodleChangeReadService(factory).list_changes(query_time=now)

        assert result.comparison_status is ComparisonStatus.AVAILABLE
        assert result.total_change_count == 0
    finally:
        engine.dispose()


def test_document_version_change_is_detected_between_queries_on_same_sync(
    tmp_path: Path,
) -> None:
    engine, factory = service_for(tmp_path / "phase-e.db")
    now = datetime.now(UTC)
    sync_finished_at = now - timedelta(hours=6)
    first_version_at = now - timedelta(hours=5)
    boundary = now - timedelta(hours=2)
    second_version_at = now - timedelta(hours=1)
    try:
        resource = RawRemoteFile("resource-cm:91", "course-1", "Lecture.pdf")
        sync_at(factory, build_snapshot(documents=(resource,)), sync_finished_at)
        with factory() as session:
            remote_file_id = session.query(RemoteFileORM).one().id
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=1,
            ingested_at=first_version_at,
        )
        add_document_version(
            factory,
            remote_file_id=remote_file_id,
            version_number=2,
            ingested_at=second_version_at,
        )

        result = MoodleChangeReadService(factory).list_changes(
            since=boundary,
            query_time=now,
        )

        assert result.comparison_status is ComparisonStatus.AVAILABLE
        assert result.from_snapshot_id == result.to_snapshot_id
        assert result.total_change_count == 1
        change = result.records[0]
        assert change.entity_type.value == "DOCUMENT"
        assert change.change_kind is ChangeKind.MODIFIED
        assert change.changed_fields == (ChangeField.DOCUMENT_VERSION,)
        assert (change.before_document_version, change.after_document_version) == (1, 2)
        assert change.detected_at == second_version_at
    finally:
        engine.dispose()
