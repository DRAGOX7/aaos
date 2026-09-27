from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aaos.academic.normalization import normalize_collection
from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
)
from aaos.moodle.changes import (
    ChangeField,
    ChangeKind,
    DocumentVersionObservation,
    EntityType,
    canonical_state,
    deserialize_state,
    diff_moodle_snapshots,
    serialize_state,
)

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def snapshot(
    *,
    courses: tuple[RawCourse, ...] = (RawCourse("c1", "Deep Learning", "CS401"),),
    assignments: tuple[RawAssignment, ...] = (),
    announcements: tuple[RawAnnouncement, ...] = (),
    documents: tuple[RawRemoteFile, ...] = (),
) -> object:
    return normalize_collection(
        CompleteSourceSnapshot(
            source_system="moodle:fixture",
            courses=courses,
            assignments=assignments,
            announcements=announcements,
            remote_files=documents,
        )
    )


def assignment(
    source_id: str = "a1",
    *,
    title: str = "Assignment 1",
    due_at: str | None = "2026-10-01T09:00:00Z",
) -> RawAssignment:
    return RawAssignment(source_id, "c1", title, due_at=due_at)


def document(source_id: str = "resource-cm:91", *, title: str = "Lecture.pdf") -> RawRemoteFile:
    return RawRemoteFile(source_id, "c1", title)


def changes(previous: object, current: object, **kwargs: object) -> tuple[object, ...]:
    return diff_moodle_snapshots(
        canonical_state(previous),
        canonical_state(current),
        from_snapshot_id=10,
        to_snapshot_id=11,
        detected_at=NOW,
        **kwargs,
    )


def test_identical_normalized_states_ignore_sync_and_entity_order_noise() -> None:
    before = snapshot(
        assignments=(assignment("a1"), assignment("a2", title="Assignment 2")),
        documents=(document("resource-cm:91"), document("resource-cm:92", title="Slides.pdf")),
    )
    after = snapshot(
        courses=(
            RawCourse("c1", "Deep Learning", "CS401", source_updated_at="2026-09-27T11:00:00Z"),
        ),
        assignments=(assignment("a2", title="Assignment 2"), assignment("a1")),
        documents=(document("resource-cm:92", title="Slides.pdf"), document("resource-cm:91")),
    )

    assert changes(before, after) == ()


def test_course_added_and_removed_by_provider_identity() -> None:
    before = snapshot()
    with_extra = snapshot(
        courses=(
            RawCourse("c1", "Deep Learning", "CS401"),
            RawCourse("c2", "Databases", "CS301"),
        )
    )

    added = changes(before, with_extra)
    removed = changes(with_extra, before)

    assert [(item.entity_type, item.change_kind, item.entity_source_id) for item in added] == [
        (EntityType.COURSE, ChangeKind.ADDED, "c2")
    ]
    assert [(item.entity_type, item.change_kind, item.entity_source_id) for item in removed] == [
        (EntityType.COURSE, ChangeKind.REMOVED, "c2")
    ]


def test_assignment_added_removed_title_and_deadline_changes_are_distinct() -> None:
    empty = snapshot()
    original = snapshot(assignments=(assignment(),))
    added = changes(empty, original)
    removed = changes(original, empty)
    title_changed = changes(
        original, snapshot(assignments=(assignment(title="Assignment 1 revised"),))
    )
    deadline_changed = changes(
        original,
        snapshot(assignments=(assignment(due_at="2026-10-02T09:00:00+00:00"),)),
    )

    assert added[0].entity_type is EntityType.ASSIGNMENT
    assert added[0].change_kind is ChangeKind.ADDED
    assert removed[0].change_kind is ChangeKind.REMOVED
    assert title_changed[0].changed_fields == (ChangeField.TITLE,)
    assert deadline_changed[0].changed_fields == (ChangeField.DUE_AT,)
    assert deadline_changed[0].before_due_at == datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    assert deadline_changed[0].after_due_at == datetime(2026, 10, 2, 9, tzinfo=timezone.utc)


def test_documents_added_removed_renamed_and_version_changed() -> None:
    empty = snapshot()
    original = snapshot(documents=(document(),))

    added = changes(empty, original)
    removed = changes(original, empty)
    renamed = changes(original, snapshot(documents=(document(title="Lecture revised.pdf"),)))
    versioned = changes(
        original,
        original,
        before_document_versions={
            "resource-cm:91": DocumentVersionObservation(1, NOW - timedelta(days=1))
        },
        after_document_versions={
            "resource-cm:91": DocumentVersionObservation(2, NOW - timedelta(hours=1))
        },
    )

    assert added[0].entity_type is EntityType.DOCUMENT
    assert added[0].change_kind is ChangeKind.ADDED
    assert removed[0].change_kind is ChangeKind.REMOVED
    assert renamed[0].changed_fields == (ChangeField.TITLE,)
    assert versioned[0].change_kind is ChangeKind.MODIFIED
    assert versioned[0].changed_fields == (ChangeField.DOCUMENT_VERSION,)
    assert (versioned[0].before_document_version, versioned[0].after_document_version) == (1, 2)


def test_new_document_with_first_version_is_one_addition() -> None:
    before = canonical_state(snapshot())
    state = canonical_state(snapshot(documents=(document(),)))
    current_version = DocumentVersionObservation(1, NOW - timedelta(minutes=2))

    result = diff_moodle_snapshots(
        before,
        state,
        from_snapshot_id=10,
        to_snapshot_id=11,
        detected_at=NOW,
        after_document_versions={"resource-cm:91": current_version},
    )

    assert len(result) == 1
    assert result[0].change_kind is ChangeKind.ADDED
    assert result[0].after_document_version == 1


def test_announcement_title_and_publication_time_changes_are_computed() -> None:
    before = snapshot(
        announcements=(
            RawAnnouncement("n1", "c1", "Quiz notice", published_at="2026-09-26T09:00:00Z"),
        )
    )
    after = snapshot(
        announcements=(
            RawAnnouncement("n1", "c1", "Quiz moved", published_at="2026-09-27T09:00:00Z"),
        )
    )

    result = changes(before, after)

    assert result[0].entity_type is EntityType.ANNOUNCEMENT
    assert result[0].change_kind is ChangeKind.MODIFIED
    assert result[0].changed_fields == (ChangeField.TITLE, ChangeField.PUBLISHED_AT)


def test_snapshot_storage_round_trip_is_closed_and_omits_operational_source_data() -> None:
    normalized = snapshot(
        assignments=(
            RawAssignment(
                "a1",
                "c1",
                "Assignment 1",
                description="private source prose",
                due_at="2026-10-01T09:00:00Z",
                source_updated_at="2026-09-27T11:00:00Z",
            ),
        ),
        documents=(
            RawRemoteFile(
                "resource-cm:91",
                "c1",
                "Lecture.pdf",
                remote_url="https://moodle.example/resource?id=91",
            ),
        ),
    )

    encoded = serialize_state(canonical_state(normalized))
    decoded = deserialize_state(encoded)

    assert decoded == canonical_state(normalized)
    assert "private source prose" not in repr(encoded)
    assert "remote_url" not in repr(encoded)
    assert "source_updated_at" not in repr(encoded)


def test_deterministic_order_and_change_provenance() -> None:
    before = snapshot()
    after = snapshot(
        assignments=(assignment("a2", title="B"), assignment("a1", title="A")),
        documents=(
            document("resource-cm:92", title="Z.pdf"),
            document("resource-cm:91", title="Y.pdf"),
        ),
    )

    result = changes(before, after)

    assert [item.entity_source_id for item in result] == [
        "a1",
        "a2",
        "resource-cm:91",
        "resource-cm:92",
    ]
    assert all(item.from_snapshot_id == 10 and item.to_snapshot_id == 11 for item in result)
    assert all(item.detected_at == NOW for item in result)
    assert [item.change_id for item in result] == ["10-11-1", "10-11-2", "10-11-3", "10-11-4"]
