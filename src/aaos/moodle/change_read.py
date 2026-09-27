"""Bounded read service for persisted complete Moodle snapshot comparisons."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from aaos.moodle.changes import (
    ComparisonStatus,
    ComparisonUnavailableReason,
    DocumentVersionObservation,
    EntityType,
    MoodleChange,
    MoodleChangeSet,
    NormalizedMoodleState,
    deserialize_state,
    diff_moodle_snapshots,
)
from aaos.persistence.academic_models import (
    AnnouncementORM,
    AssignmentORM,
    CourseORM,
    RemoteFileORM,
)
from aaos.persistence.document_models import DocumentORM, DocumentVersionORM
from aaos.persistence.moodle_sync_models import MoodleSyncSnapshotORM

MAX_CHANGE_PAGE_SIZE = 15
MAX_CHANGE_OFFSET = 200


class MoodleChangeReadService:
    """Compare safe persisted states and preserve local entity provenance."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    def list_changes(
        self,
        *,
        course_id: int | None = None,
        since: datetime | None = None,
        limit: int = MAX_CHANGE_PAGE_SIZE,
        offset: int = 0,
        query_time: datetime | None = None,
    ) -> MoodleChangeSet:
        _validate_request(course_id, since, limit, offset)
        query_time = _utc(query_time or datetime.now(timezone.utc), "query_time")
        boundary = _utc(since, "since") if since is not None else None
        session = self._session_factory()
        try:
            source_system, scoped_course_id, source_error = self._resolve_source(session, course_id)
            if source_system is None:
                return MoodleChangeSet.unavailable(
                    source_error or ComparisonUnavailableReason.NO_BASELINE,
                    offset=offset,
                    limit=limit,
                )

            latest_attempt = (
                session.query(MoodleSyncSnapshotORM)
                .filter_by(source_system=source_system)
                .order_by(MoodleSyncSnapshotORM.id.desc())
                .first()
            )
            if latest_attempt is None:
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.NO_BASELINE,
                    offset=offset,
                    limit=limit,
                )
            if latest_attempt.status != "COMPLETE":
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.LATEST_SYNC_INCOMPLETE,
                    offset=offset,
                    limit=limit,
                )
            if boundary is not None:
                boundary_attempt = (
                    session.query(MoodleSyncSnapshotORM)
                    .filter(
                        MoodleSyncSnapshotORM.source_system == source_system,
                        or_(
                            MoodleSyncSnapshotORM.finished_at <= boundary,
                            and_(
                                MoodleSyncSnapshotORM.status == "RUNNING",
                                MoodleSyncSnapshotORM.started_at <= boundary,
                            ),
                        ),
                    )
                    .order_by(MoodleSyncSnapshotORM.id.desc())
                    .first()
                )
                if boundary_attempt is not None and boundary_attempt.status != "COMPLETE":
                    return MoodleChangeSet.unavailable(
                        ComparisonUnavailableReason.LATEST_SYNC_INCOMPLETE,
                        offset=offset,
                        limit=limit,
                    )

            complete_runs = (
                session.query(MoodleSyncSnapshotORM)
                .filter_by(source_system=source_system, status="COMPLETE")
                .order_by(MoodleSyncSnapshotORM.id.desc())
                .limit(2)
                .all()
            )
            current = complete_runs[0] if complete_runs else None
            if current is None or current.finished_at is None:
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.NO_BASELINE,
                    offset=offset,
                    limit=limit,
                )
            if boundary is None:
                previous = complete_runs[1] if len(complete_runs) > 1 else None
            else:
                previous = (
                    session.query(MoodleSyncSnapshotORM)
                    .filter(
                        MoodleSyncSnapshotORM.source_system == source_system,
                        MoodleSyncSnapshotORM.status == "COMPLETE",
                        MoodleSyncSnapshotORM.finished_at <= boundary,
                    )
                    .order_by(
                        MoodleSyncSnapshotORM.finished_at.desc(),
                        MoodleSyncSnapshotORM.id.desc(),
                    )
                    .first()
                )
            if previous is None or previous.finished_at is None:
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.NO_BASELINE,
                    offset=offset,
                    limit=limit,
                )

            try:
                previous_state = _stored_state(previous)
                current_state = _stored_state(current)
            except (TypeError, ValueError):
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.HISTORY_INVALID,
                    offset=offset,
                    limit=limit,
                )
            if (
                previous.snapshot_version != 1
                or current.snapshot_version != 1
                or previous_state.source_system != source_system
                or current_state.source_system != source_system
            ):
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.HISTORY_INVALID,
                    offset=offset,
                    limit=limit,
                )

            if previous.id == current.id:
                all_changes: tuple[MoodleChange, ...] = ()
            else:
                ids = {
                    item.source_id for item in (*previous_state.documents, *current_state.documents)
                }
                before_versions = _document_versions(
                    session,
                    source_system=source_system,
                    source_ids=ids,
                    as_of=boundary or previous.finished_at,
                )
                after_versions = _document_versions(
                    session,
                    source_system=source_system,
                    source_ids=ids,
                    as_of=query_time,
                )
                try:
                    all_changes = diff_moodle_snapshots(
                        previous_state,
                        current_state,
                        from_snapshot_id=previous.id,
                        to_snapshot_id=current.id,
                        detected_at=current.finished_at,
                        before_document_versions=before_versions,
                        after_document_versions=after_versions,
                    )
                except (TypeError, ValueError):
                    return MoodleChangeSet.unavailable(
                        ComparisonUnavailableReason.HISTORY_INVALID,
                        offset=offset,
                        limit=limit,
                    )
            if scoped_course_id is not None:
                all_changes = tuple(
                    change for change in all_changes if change.course_source_id == scoped_course_id
                )
            try:
                changes = self._with_entity_references(session, source_system, all_changes)
            except ValueError:
                return MoodleChangeSet.unavailable(
                    ComparisonUnavailableReason.HISTORY_INVALID,
                    offset=offset,
                    limit=limit,
                )
            added_count = sum(change.change_kind.value == "ADDED" for change in changes)
            removed_count = sum(change.change_kind.value == "REMOVED" for change in changes)
            modified_count = sum(change.change_kind.value == "MODIFIED" for change in changes)
            page = changes[offset : offset + limit]
            return MoodleChangeSet(
                comparison_status=ComparisonStatus.AVAILABLE,
                unavailable_reason=None,
                from_snapshot_id=previous.id,
                to_snapshot_id=current.id,
                changed_courses_count=len({change.course_source_id for change in changes}),
                added_count=added_count,
                removed_count=removed_count,
                modified_count=modified_count,
                total_change_count=len(changes),
                records=page,
                offset=offset,
                limit=limit,
                has_more=offset + limit < len(changes),
            )
        finally:
            session.close()

    @staticmethod
    def _resolve_source(
        session: Session,
        course_id: int | None,
    ) -> tuple[str | None, str | None, ComparisonUnavailableReason | None]:
        if course_id is not None:
            course = session.get(CourseORM, course_id)
            if course is None:
                return None, None, ComparisonUnavailableReason.SOURCE_NOT_FOUND
            return course.source_system, course.source_id, None
        source_systems = tuple(
            row[0]
            for row in session.query(MoodleSyncSnapshotORM.source_system)
            .distinct()
            .order_by(MoodleSyncSnapshotORM.source_system.asc())
            .all()
        )
        if len(source_systems) == 1:
            return source_systems[0], None, None
        if not source_systems:
            return None, None, ComparisonUnavailableReason.NO_BASELINE
        return None, None, ComparisonUnavailableReason.SOURCE_AMBIGUOUS

    @staticmethod
    def _with_entity_references(
        session: Session,
        source_system: str,
        changes: tuple[MoodleChange, ...],
    ) -> tuple[MoodleChange, ...]:
        if not changes:
            return ()
        course_rows = {
            row.source_id: row
            for row in session.query(CourseORM).filter_by(source_system=source_system).all()
        }
        assignment_rows = {
            row.source_id: row
            for row in session.query(AssignmentORM).filter_by(source_system=source_system).all()
        }
        announcement_rows = {
            row.source_id: row
            for row in session.query(AnnouncementORM).filter_by(source_system=source_system).all()
        }
        document_rows = {
            row.source_id: row
            for row in session.query(RemoteFileORM).filter_by(source_system=source_system).all()
        }
        state_models: Mapping[
            EntityType,
            Mapping[str, CourseORM | AssignmentORM | AnnouncementORM | RemoteFileORM],
        ] = {
            EntityType.COURSE: course_rows,
            EntityType.ASSIGNMENT: assignment_rows,
            EntityType.ANNOUNCEMENT: announcement_rows,
            EntityType.DOCUMENT: document_rows,
        }
        enriched: list[MoodleChange] = []
        for change in changes:
            entity = state_models[change.entity_type].get(change.entity_source_id)
            course = course_rows.get(change.course_source_id)
            if entity is None or course is None:
                raise ValueError("change history has no matching academic source records")
            if not (change.after_title or change.before_title):
                raise ValueError("change history has no entity title")
            enriched.append(
                replace(
                    change,
                    course_id=course.id,
                    entity_id=entity.id,
                    course_title=course.title,
                )
            )
        return tuple(enriched)


def _stored_state(row: MoodleSyncSnapshotORM) -> NormalizedMoodleState:
    if row.snapshot_state is None:
        raise ValueError("completed Moodle sync has no stored state")
    return deserialize_state(row.snapshot_state)


def _document_versions(
    session: Session,
    *,
    source_system: str,
    source_ids: set[str],
    as_of: datetime,
) -> dict[str, DocumentVersionObservation]:
    if not source_ids:
        return {}
    rows = (
        session.query(
            RemoteFileORM.source_id,
            DocumentVersionORM.version_number,
            DocumentVersionORM.ingested_at,
        )
        .join(DocumentORM, DocumentORM.remote_file_id == RemoteFileORM.id)
        .join(DocumentVersionORM, DocumentVersionORM.document_id == DocumentORM.id)
        .filter(
            RemoteFileORM.source_system == source_system,
            RemoteFileORM.source_id.in_(source_ids),
            DocumentVersionORM.ingested_at <= as_of,
        )
        .order_by(
            RemoteFileORM.source_id.asc(),
            DocumentVersionORM.version_number.desc(),
            DocumentVersionORM.ingested_at.desc(),
        )
        .all()
    )
    versions: dict[str, DocumentVersionObservation] = {}
    for source_id, version_number, ingested_at in rows:
        versions.setdefault(
            source_id,
            DocumentVersionObservation(version_number, ingested_at),
        )
    return versions


def _validate_request(
    course_id: int | None,
    since: datetime | None,
    limit: int,
    offset: int,
) -> None:
    if course_id is not None and (
        isinstance(course_id, bool) or not isinstance(course_id, int) or course_id <= 0
    ):
        raise ValueError("course_id must be a positive integer or None")
    if since is not None and (since.tzinfo is None or since.utcoffset() is None):
        raise ValueError("since must include a timezone")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_CHANGE_PAGE_SIZE
    ):
        raise ValueError("limit must be between one and fifteen")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= MAX_CHANGE_OFFSET
    ):
        raise ValueError("offset must be between zero and two hundred")


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(timezone.utc)


__all__ = ["MAX_CHANGE_OFFSET", "MAX_CHANGE_PAGE_SIZE", "MoodleChangeReadService"]
