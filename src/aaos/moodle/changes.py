"""Typed, deterministic diffs over complete normalized Moodle snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping, TypeAlias

from aaos.academic.models import (
    _NORMALIZED_SNAPSHOT_TOKEN,
    CanonicalSnapshot,
)


class EntityType(StrEnum):
    COURSE = "COURSE"
    ASSIGNMENT = "ASSIGNMENT"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    DOCUMENT = "DOCUMENT"


class ChangeKind(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"


class ChangeField(StrEnum):
    TITLE = "title"
    COURSE_CODE = "code"
    DUE_AT = "due_at"
    PUBLISHED_AT = "published_at"
    PARENT_COURSE = "course"
    DOCUMENT_VERSION = "document_version"


class ComparisonStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "CHANGE_COMPARISON_UNAVAILABLE"


class ComparisonUnavailableReason(StrEnum):
    NO_BASELINE = "NO_BASELINE"
    LATEST_SYNC_INCOMPLETE = "LATEST_SYNC_INCOMPLETE"
    UNSUPPORTED_TIME_WINDOW = "UNSUPPORTED_TIME_WINDOW"
    SOURCE_AMBIGUOUS = "SOURCE_AMBIGUOUS"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    HISTORY_INVALID = "HISTORY_INVALID"


@dataclass(frozen=True, slots=True)
class CourseState:
    source_id: str
    title: str
    code: str | None


@dataclass(frozen=True, slots=True)
class AssignmentState:
    source_id: str
    course_source_id: str
    title: str
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class AnnouncementState:
    source_id: str
    course_source_id: str
    title: str
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class DocumentState:
    source_id: str
    course_source_id: str
    title: str


@dataclass(frozen=True, slots=True)
class NormalizedMoodleState:
    source_system: str
    courses: tuple[CourseState, ...]
    assignments: tuple[AssignmentState, ...]
    announcements: tuple[AnnouncementState, ...]
    documents: tuple[DocumentState, ...]
    _validation_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _safe_text(self.source_system, "source_system", maximum=100)
        for label, records in (
            ("course", self.courses),
            ("assignment", self.assignments),
            ("announcement", self.announcements),
            ("document", self.documents),
        ):
            if not isinstance(records, tuple):
                raise TypeError(f"{label} state must be a tuple")
            ids: set[str] = set()
            for record in records:
                if record.source_id in ids:
                    raise ValueError(f"duplicate {label} provider identity")
                ids.add(record.source_id)
        course_ids = {record.source_id for record in self.courses}
        for records in (self.assignments, self.announcements, self.documents):
            for record in records:
                if record.course_source_id not in course_ids:
                    raise ValueError("child record references a missing canonical course")


@dataclass(frozen=True, slots=True)
class DocumentVersionObservation:
    version_number: int
    ingested_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.version_number, bool)
            or not isinstance(self.version_number, int)
            or self.version_number <= 0
        ):
            raise ValueError("document version number must be positive")
        _aware_utc(self.ingested_at, "document version time")


@dataclass(frozen=True, slots=True)
class MoodleChange:
    change_id: str
    from_snapshot_id: int
    to_snapshot_id: int
    entity_type: EntityType
    entity_source_id: str
    course_source_id: str
    change_kind: ChangeKind
    changed_fields: tuple[ChangeField, ...]
    before_title: str | None
    after_title: str | None
    before_course_code: str | None
    after_course_code: str | None
    before_due_at: datetime | None
    after_due_at: datetime | None
    before_published_at: datetime | None
    after_published_at: datetime | None
    before_document_version: int | None
    after_document_version: int | None
    detected_at: datetime
    course_id: int | None = None
    entity_id: int | None = None
    course_title: str | None = None

    def __post_init__(self) -> None:
        if not self.change_id or not isinstance(self.change_id, str):
            raise ValueError("change_id must be non-blank text")
        _positive_id(self.from_snapshot_id, "from_snapshot_id")
        _positive_id(self.to_snapshot_id, "to_snapshot_id")
        if not isinstance(self.entity_type, EntityType) or not isinstance(
            self.change_kind, ChangeKind
        ):
            raise TypeError("change type and kind must use their closed enums")
        if (
            not isinstance(self.changed_fields, tuple)
            or not self.changed_fields
            or any(not isinstance(field, ChangeField) for field in self.changed_fields)
        ):
            raise ValueError("changed_fields must be a non-empty tuple of closed fields")
        _safe_text(self.entity_source_id, "entity_source_id", maximum=255)
        _safe_text(self.course_source_id, "course_source_id", maximum=255)
        _aware_utc(self.detected_at, "detected_at")
        for label, value in (("course_id", self.course_id), ("entity_id", self.entity_id)):
            if value is not None:
                _positive_id(value, label)
        if self.course_title is not None:
            _safe_text(self.course_title, "course_title")


@dataclass(frozen=True, slots=True)
class MoodleChangeSet:
    comparison_status: ComparisonStatus
    unavailable_reason: ComparisonUnavailableReason | None
    from_snapshot_id: int | None
    to_snapshot_id: int | None
    changed_courses_count: int
    added_count: int
    removed_count: int
    modified_count: int
    total_change_count: int
    records: tuple[MoodleChange, ...]
    offset: int
    limit: int
    has_more: bool
    # Items that were archived together with a course in this comparison. They
    # are counted, not listed; the course itself is the listed change.
    archived_item_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.comparison_status, ComparisonStatus):
            raise TypeError("comparison_status must be a ComparisonStatus")
        if self.comparison_status is ComparisonStatus.UNAVAILABLE:
            if (
                not isinstance(self.unavailable_reason, ComparisonUnavailableReason)
                or self.from_snapshot_id is not None
                or self.to_snapshot_id is not None
                or self.records
                or self.total_change_count
                or self.has_more
            ):
                raise ValueError("unavailable comparisons must not contain change evidence")
        elif self.unavailable_reason is not None:
            raise ValueError("available comparisons cannot have an unavailable reason")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.changed_courses_count,
                self.added_count,
                self.removed_count,
                self.modified_count,
                self.total_change_count,
                self.offset,
                self.archived_item_count,
            )
        ):
            raise ValueError("change counts and offset must be non-negative integers")
        if not 1 <= self.limit <= 15 or len(self.records) > self.limit:
            raise ValueError("change result exceeds its bounded page size")
        if not isinstance(self.records, tuple) or any(
            not isinstance(record, MoodleChange) for record in self.records
        ):
            raise TypeError("records must be a tuple of MoodleChange values")
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be a bool")

    @classmethod
    def unavailable(
        cls,
        reason: ComparisonUnavailableReason,
        *,
        offset: int = 0,
        limit: int = 15,
    ) -> MoodleChangeSet:
        return cls(
            comparison_status=ComparisonStatus.UNAVAILABLE,
            unavailable_reason=reason,
            from_snapshot_id=None,
            to_snapshot_id=None,
            changed_courses_count=0,
            added_count=0,
            removed_count=0,
            modified_count=0,
            total_change_count=0,
            records=(),
            offset=offset,
            limit=limit,
            has_more=False,
        )


_StateRecord: TypeAlias = CourseState | AssignmentState | AnnouncementState | DocumentState
_RECORD_ORDER = {
    EntityType.COURSE: 0,
    EntityType.ASSIGNMENT: 1,
    EntityType.ANNOUNCEMENT: 2,
    EntityType.DOCUMENT: 3,
}
_FIELD_ORDER = {field: index for index, field in enumerate(ChangeField)}
_NORMALIZED_MOODLE_STATE_TOKEN = object()


def canonical_state(snapshot: CanonicalSnapshot) -> NormalizedMoodleState:
    """Project a normalized snapshot onto the small academic change vocabulary."""
    if not isinstance(snapshot, CanonicalSnapshot) or snapshot._validation_token is not (
        _NORMALIZED_SNAPSHOT_TOKEN
    ):
        raise ValueError("change snapshots must come from complete normalization")
    return NormalizedMoodleState(
        source_system=snapshot.source_system,
        courses=tuple(
            CourseState(record.source_id, record.title, record.code) for record in snapshot.courses
        ),
        assignments=tuple(
            AssignmentState(record.source_id, record.course_source_id, record.title, record.due_at)
            for record in snapshot.assignments
        ),
        announcements=tuple(
            AnnouncementState(
                record.source_id,
                record.course_source_id,
                record.title,
                record.published_at,
            )
            for record in snapshot.announcements
        ),
        documents=tuple(
            DocumentState(record.source_id, record.course_source_id, record.title)
            for record in snapshot.remote_files
        ),
        _validation_token=_NORMALIZED_MOODLE_STATE_TOKEN,
    )


def serialize_state(state: NormalizedMoodleState) -> dict[str, object]:
    """Return a compact whitelisted JSON value with no source prose or URLs."""
    if not isinstance(state, NormalizedMoodleState):
        raise TypeError("state must be a NormalizedMoodleState")
    return {
        "source_system": state.source_system,
        "courses": [
            {"source_id": item.source_id, "title": item.title, "code": item.code}
            for item in state.courses
        ],
        "assignments": [
            {
                "source_id": item.source_id,
                "course_source_id": item.course_source_id,
                "title": item.title,
                "due_at": _serialize_time(item.due_at),
            }
            for item in state.assignments
        ],
        "announcements": [
            {
                "source_id": item.source_id,
                "course_source_id": item.course_source_id,
                "title": item.title,
                "published_at": _serialize_time(item.published_at),
            }
            for item in state.announcements
        ],
        "documents": [
            {
                "source_id": item.source_id,
                "course_source_id": item.course_source_id,
                "title": item.title,
            }
            for item in state.documents
        ],
    }


def deserialize_state(value: object) -> NormalizedMoodleState:
    """Load and strictly validate the closed persisted snapshot shape."""
    if not isinstance(value, Mapping) or set(value) != {
        "source_system",
        "courses",
        "assignments",
        "announcements",
        "documents",
    }:
        raise ValueError("stored Moodle state has an unsupported shape")
    source_system = _safe_text(value["source_system"], "source_system", maximum=100)
    courses = tuple(
        CourseState(
            _safe_text(row.get("source_id"), "course source_id", maximum=255),
            _safe_text(row.get("title"), "course title"),
            _optional_safe_text(row.get("code"), "course code"),
        )
        for row in _rows(value["courses"], {"source_id", "title", "code"})
    )
    assignments = tuple(
        AssignmentState(
            _safe_text(row.get("source_id"), "assignment source_id", maximum=255),
            _safe_text(row.get("course_source_id"), "assignment course_id", maximum=255),
            _safe_text(row.get("title"), "assignment title"),
            _parse_time(row.get("due_at"), "assignment due_at"),
        )
        for row in _rows(value["assignments"], {"source_id", "course_source_id", "title", "due_at"})
    )
    announcements = tuple(
        AnnouncementState(
            _safe_text(row.get("source_id"), "announcement source_id", maximum=255),
            _safe_text(row.get("course_source_id"), "announcement course_id", maximum=255),
            _safe_text(row.get("title"), "announcement title"),
            _parse_time(row.get("published_at"), "announcement published_at"),
        )
        for row in _rows(
            value["announcements"], {"source_id", "course_source_id", "title", "published_at"}
        )
    )
    documents = tuple(
        DocumentState(
            _safe_text(row.get("source_id"), "document source_id", maximum=255),
            _safe_text(row.get("course_source_id"), "document course_id", maximum=255),
            _safe_text(row.get("title"), "document title"),
        )
        for row in _rows(value["documents"], {"source_id", "course_source_id", "title"})
    )
    return NormalizedMoodleState(source_system, courses, assignments, announcements, documents)


def diff_moodle_snapshots(
    previous: NormalizedMoodleState,
    current: NormalizedMoodleState,
    *,
    from_snapshot_id: int,
    to_snapshot_id: int,
    detected_at: datetime,
    before_document_versions: Mapping[str, DocumentVersionObservation] | None = None,
    after_document_versions: Mapping[str, DocumentVersionObservation] | None = None,
) -> tuple[MoodleChange, ...]:
    """Compute a stable O(n) keyed diff over two comparable normalized states."""
    if not isinstance(previous, NormalizedMoodleState) or not isinstance(
        current, NormalizedMoodleState
    ):
        raise TypeError("both sides must be normalized Moodle states")
    if previous.source_system != current.source_system:
        raise ValueError("Moodle snapshots from different source namespaces are incomparable")
    _positive_id(from_snapshot_id, "from_snapshot_id")
    _positive_id(to_snapshot_id, "to_snapshot_id")
    if from_snapshot_id > to_snapshot_id:
        raise ValueError("current snapshot ID must follow previous snapshot ID")
    if from_snapshot_id == to_snapshot_id and previous != current:
        raise ValueError("equal snapshot IDs must describe identical normalized states")
    detected_at = _aware_utc(detected_at, "detected_at")
    before_versions = before_document_versions or {}
    after_versions = after_document_versions or {}
    _validate_version_map(before_versions)
    _validate_version_map(after_versions)

    pending: list[tuple[tuple[object, ...], MoodleChange]] = []
    before_courses = {item.source_id: item for item in previous.courses}
    after_courses = {item.source_id: item for item in current.courses}
    _collect_changes(
        pending,
        EntityType.COURSE,
        before_courses,
        after_courses,
        from_snapshot_id,
        to_snapshot_id,
        detected_at,
        before_versions,
        after_versions,
    )
    for entity_type, before_rows, after_rows in (
        (EntityType.ASSIGNMENT, previous.assignments, current.assignments),
        (EntityType.ANNOUNCEMENT, previous.announcements, current.announcements),
        (EntityType.DOCUMENT, previous.documents, current.documents),
    ):
        _collect_changes(
            pending,
            entity_type,
            {item.source_id: item for item in before_rows},
            {item.source_id: item for item in after_rows},
            from_snapshot_id,
            to_snapshot_id,
            detected_at,
            before_versions,
            after_versions,
        )

    pending.sort(key=lambda item: item[0])
    return tuple(
        MoodleChange(
            change_id=f"{from_snapshot_id}-{to_snapshot_id}-{index}",
            from_snapshot_id=change.from_snapshot_id,
            to_snapshot_id=change.to_snapshot_id,
            entity_type=change.entity_type,
            entity_source_id=change.entity_source_id,
            course_source_id=change.course_source_id,
            change_kind=change.change_kind,
            changed_fields=change.changed_fields,
            before_title=change.before_title,
            after_title=change.after_title,
            before_course_code=change.before_course_code,
            after_course_code=change.after_course_code,
            before_due_at=change.before_due_at,
            after_due_at=change.after_due_at,
            before_published_at=change.before_published_at,
            after_published_at=change.after_published_at,
            before_document_version=change.before_document_version,
            after_document_version=change.after_document_version,
            detected_at=change.detected_at,
        )
        for index, (_, change) in enumerate(pending, start=1)
    )


def _collect_changes(
    pending: list[tuple[tuple[object, ...], MoodleChange]],
    entity_type: EntityType,
    before: Mapping[str, _StateRecord],
    after: Mapping[str, _StateRecord],
    from_snapshot_id: int,
    to_snapshot_id: int,
    detected_at: datetime,
    before_versions: Mapping[str, DocumentVersionObservation],
    after_versions: Mapping[str, DocumentVersionObservation],
) -> None:
    for source_id in before.keys() | after.keys():
        old = before.get(source_id)
        new = after.get(source_id)
        old_version = (
            before_versions.get(source_id)
            if entity_type is EntityType.DOCUMENT and old is not None
            else None
        )
        new_version = (
            after_versions.get(source_id)
            if entity_type is EntityType.DOCUMENT and new is not None
            else None
        )
        kind = (
            ChangeKind.ADDED
            if old is None
            else ChangeKind.REMOVED
            if new is None
            else ChangeKind.MODIFIED
        )
        fields = _changed_fields(entity_type, old, new, old_version, new_version, kind)
        if not fields:
            continue
        current_record = new or old
        assert current_record is not None
        if entity_type is EntityType.COURSE:
            if not isinstance(current_record, CourseState):
                raise ValueError("course identity map contains a child state")
            course_source_id = current_record.source_id
        else:
            if isinstance(current_record, CourseState):
                raise ValueError("child identity map contains a course state")
            course_source_id = current_record.course_source_id
        observed_at = (
            new_version.ingested_at
            if entity_type is EntityType.DOCUMENT
            and ChangeField.DOCUMENT_VERSION in fields
            and new_version is not None
            else detected_at
        )
        change = MoodleChange(
            change_id="pending",
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            entity_type=entity_type,
            entity_source_id=source_id,
            course_source_id=course_source_id,
            change_kind=kind,
            changed_fields=fields,
            before_title=_record_title(old),
            after_title=_record_title(new),
            before_course_code=old.code if isinstance(old, CourseState) else None,
            after_course_code=new.code if isinstance(new, CourseState) else None,
            before_due_at=old.due_at if isinstance(old, AssignmentState) else None,
            after_due_at=new.due_at if isinstance(new, AssignmentState) else None,
            before_published_at=(old.published_at if isinstance(old, AnnouncementState) else None),
            after_published_at=(new.published_at if isinstance(new, AnnouncementState) else None),
            before_document_version=old_version.version_number if old_version is not None else None,
            after_document_version=new_version.version_number if new_version is not None else None,
            detected_at=observed_at,
        )
        pending.append(
            (
                (
                    course_source_id,
                    _RECORD_ORDER[entity_type],
                    source_id,
                    kind.value,
                    tuple(_FIELD_ORDER[field] for field in fields),
                ),
                change,
            )
        )


def _changed_fields(
    entity_type: EntityType,
    before: _StateRecord | None,
    after: _StateRecord | None,
    before_version: DocumentVersionObservation | None,
    after_version: DocumentVersionObservation | None,
    kind: ChangeKind,
) -> tuple[ChangeField, ...]:
    values: list[ChangeField] = []
    if before is None or after is None:
        record = after or before
        assert record is not None
        values.append(ChangeField.TITLE)
        if entity_type is EntityType.COURSE and isinstance(record, CourseState) and record.code:
            values.append(ChangeField.COURSE_CODE)
        if not isinstance(record, CourseState) and record.course_source_id:
            values.append(ChangeField.PARENT_COURSE)
        if entity_type is EntityType.ASSIGNMENT and isinstance(record, AssignmentState):
            if record.due_at is not None:
                values.append(ChangeField.DUE_AT)
        if entity_type is EntityType.ANNOUNCEMENT and isinstance(record, AnnouncementState):
            if record.published_at is not None:
                values.append(ChangeField.PUBLISHED_AT)
        if entity_type is EntityType.DOCUMENT and _version_number(
            before_version
        ) != _version_number(after_version):
            values.append(ChangeField.DOCUMENT_VERSION)
        return tuple(sorted(values, key=_FIELD_ORDER.__getitem__))

    if _record_title(before) != _record_title(after):
        values.append(ChangeField.TITLE)
    if isinstance(before, CourseState) and isinstance(after, CourseState):
        if before.code != after.code:
            values.append(ChangeField.COURSE_CODE)
    elif isinstance(before, AssignmentState) and isinstance(after, AssignmentState):
        if before.course_source_id != after.course_source_id:
            values.append(ChangeField.PARENT_COURSE)
        if before.due_at != after.due_at:
            values.append(ChangeField.DUE_AT)
    elif isinstance(before, AnnouncementState) and isinstance(after, AnnouncementState):
        if before.course_source_id != after.course_source_id:
            values.append(ChangeField.PARENT_COURSE)
        if before.published_at != after.published_at:
            values.append(ChangeField.PUBLISHED_AT)
    elif isinstance(before, DocumentState) and isinstance(after, DocumentState):
        if before.course_source_id != after.course_source_id:
            values.append(ChangeField.PARENT_COURSE)
        if _version_number(before_version) != _version_number(after_version):
            values.append(ChangeField.DOCUMENT_VERSION)
    return tuple(sorted(values, key=_FIELD_ORDER.__getitem__))


def _record_title(record: _StateRecord | None) -> str | None:
    return record.title if record is not None else None


def _version_number(value: DocumentVersionObservation | None) -> int | None:
    return value.version_number if value is not None else None


def _validate_version_map(values: Mapping[str, DocumentVersionObservation]) -> None:
    for source_id, version in values.items():
        _safe_text(source_id, "document source_id", maximum=255)
        if not isinstance(version, DocumentVersionObservation):
            raise TypeError("document version maps must contain typed version observations")


def _rows(value: object, keys: set[str]) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError("stored entity collection must be a JSON array")
    rows: list[Mapping[str, object]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != keys:
            raise ValueError("stored entity record has an unsupported shape")
        rows.append(row)
    return tuple(rows)


def _safe_text(value: object, label: str, *, maximum: int | None = None) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or (maximum is not None and len(value) > maximum)
    ):
        raise ValueError(f"{label} must be bounded safe text")
    return value


def _optional_safe_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _safe_text(value, label)


def _serialize_time(value: datetime | None) -> str | None:
    return _aware_utc(value, "timestamp").isoformat().replace("+00:00", "Z") if value else None


def _parse_time(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"stored {label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"stored {label} must be an ISO-8601 timestamp") from error
    return _aware_utc(parsed, label)


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(timezone.utc)


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


__all__ = [
    "AnnouncementState",
    "AssignmentState",
    "ChangeField",
    "ChangeKind",
    "ComparisonStatus",
    "ComparisonUnavailableReason",
    "CourseState",
    "DocumentState",
    "DocumentVersionObservation",
    "EntityType",
    "MoodleChange",
    "MoodleChangeSet",
    "NormalizedMoodleState",
    "canonical_state",
    "deserialize_state",
    "diff_moodle_snapshots",
    "serialize_state",
]
