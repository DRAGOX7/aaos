from datetime import datetime, timezone

from aaos.web.activity import (
    ActivityCategory,
    ActivityCursor,
    ActivityEventCode,
    ActivitySourceRow,
    ActivityStatus,
    decode_activity_cursor,
    encode_activity_cursor,
)
from aaos.web.activity_read import ActivityReadService


class FakeActivityRepository:
    def __init__(self, rows: tuple[ActivitySourceRow, ...]) -> None:
        self.rows = rows

    def list_source_rows(
        self, *, before: ActivityCursor | None, after: ActivityCursor | None, limit: int
    ) -> tuple[ActivitySourceRow, ...]:
        rows = sorted(
            self.rows,
            key=lambda item: (
                item.occurred_at,
                1 if item.source_kind == "ACTION_EVENT" else 0,
                item.stable_source_id,
                item.source_sequence,
            ),
        )
        if before is not None:
            rows = [
                row
                for row in rows
                if (row.occurred_at, 1, row.stable_source_id, row.source_sequence) < before
            ]
            return tuple(reversed(rows[-limit:]))
        if after is not None:
            return tuple(
                row
                for row in rows
                if (row.occurred_at, 1, row.stable_source_id, row.source_sequence) > after
            )[:limit]
        return tuple(reversed(rows[-limit:]))

    def current_high_watermark(self) -> ActivityCursor | None:
        if not self.rows:
            return None
        row = max(
            self.rows,
            key=lambda item: (item.occurred_at, item.stable_source_id, item.source_sequence),
        )
        return ActivityCursor(
            row.occurred_at,
            1 if row.source_kind == "ACTION_EVENT" else 0,
            row.stable_source_id,
            row.source_sequence,
        )


def _row(
    *,
    event_code: ActivityEventCode,
    event_id: str,
    occurred_at: datetime,
    task_id: str = "task-1",
    status: ActivityStatus | None = None,
) -> ActivitySourceRow:
    return ActivitySourceRow(
        source_kind="ACTION_EVENT",
        occurred_at=occurred_at,
        stable_source_id=event_id,
        source_sequence=1,
        task_id=task_id,
        approval_id=None,
        action_event_code=event_code,
        task_creation_state=None,
        task_failure_code=None,
        approval_status=status,
        operation_status=status,
        typed_detail=None,
    )


def test_activity_history_projects_newest_first_and_exposes_opaque_cursor() -> None:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    repository = FakeActivityRepository(
        (
            _row(
                event_code=ActivityEventCode.ACTION_PREPARED,
                event_id="event-a",
                occurred_at=timestamp,
            ),
            _row(event_code=ActivityEventCode.EXECUTING, event_id="event-b", occurred_at=timestamp),
        )
    )
    service = ActivityReadService(repository)

    response = service.list_history(limit=2)

    assert [event.event_code for event in response.events] == [
        ActivityEventCode.EXECUTING,
        ActivityEventCode.ACTION_PREPARED,
    ]
    assert response.next_cursor is not None
    assert "action_id" not in response.next_cursor
    assert decode_activity_cursor(response.next_cursor)[2] == "event-a"


def test_activity_history_uses_strict_before_cursor() -> None:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    rows = (
        _row(
            event_code=ActivityEventCode.ACTION_PREPARED, event_id="event-a", occurred_at=timestamp
        ),
        _row(event_code=ActivityEventCode.EXECUTING, event_id="event-b", occurred_at=timestamp),
    )
    repository = FakeActivityRepository(rows)
    service = ActivityReadService(repository)
    first = service.list_history(limit=1)

    assert first.next_cursor is not None
    second = service.list_history(limit=1, before=decode_activity_cursor(first.next_cursor))

    assert [event.event_code for event in second.events] == [ActivityEventCode.ACTION_PREPARED]


def test_activity_projection_keeps_unknown_outcome_distinct() -> None:
    row = _row(
        event_code=ActivityEventCode.UNKNOWN_OUTCOME,
        event_id="event-unknown",
        occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        status=ActivityStatus.UNKNOWN_OUTCOME,
    )

    event = ActivityReadService(FakeActivityRepository((row,))).list_history(limit=1).events[0]

    assert event.category is ActivityCategory.INTEGRATION
    assert event.status is ActivityStatus.UNKNOWN_OUTCOME
    assert event.event_code is ActivityEventCode.UNKNOWN_OUTCOME


def test_cursor_is_stable_for_same_projected_event() -> None:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    row = _row(
        event_code=ActivityEventCode.ACTION_PREPARED, event_id="event-a", occurred_at=timestamp
    )
    service = ActivityReadService(FakeActivityRepository((row,)))

    response = service.list_history(limit=1)

    assert response.next_cursor == encode_activity_cursor(
        ActivityCursor(timestamp, 1, "event-a", 1)
    )
