import asyncio
from datetime import datetime, timezone

from aaos.web.activity import (
    ActivityCategory,
    ActivityCursor,
    ActivityEvent,
    ActivityEventCode,
    ActivityStatus,
    encode_activity_cursor,
)
from aaos.web.activity_stream import iter_activity_sse


def _event(event_id: str) -> ActivityEvent:
    return ActivityEvent(
        schema_version="phase7-activity-v1",
        activity_id=event_id,
        occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        category=ActivityCategory.TASK,
        event_code=ActivityEventCode.TASK_CREATED,
        status=ActivityStatus.PENDING,
        resource_type=None,
        resource_id=None,
        title="Task created",
        detail=None,
        context=None,
    )


class FakeRequest:
    def __init__(self, disconnect_after: int = 2) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls >= self.disconnect_after


class FakeStreamService:
    def __init__(self) -> None:
        self.calls: list[tuple[ActivityCursor | None, int]] = []

    def current_high_watermark(self) -> ActivityCursor | None:
        return None

    def tail_with_cursors(
        self, *, after: ActivityCursor | None, limit: int
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]:
        self.calls.append((after, limit))
        if len(self.calls) == 1:
            return (
                (_event("event-1"), _cursor("event-1")),
                (_event("event-2"), _cursor("event-2")),
            )
        return ()


def _cursor(source_id: str) -> ActivityCursor:
    return ActivityCursor(
        datetime(2026, 9, 18, tzinfo=timezone.utc),
        1,
        source_id,
        1,
    )


def test_sse_emits_activity_event_and_stops_after_disconnect() -> None:
    async def collect() -> list[bytes]:
        request = FakeRequest()
        service = FakeStreamService()

        async def sleep(_: float) -> None:
            return None

        def clock() -> datetime:
            return datetime(2026, 9, 18, tzinfo=timezone.utc)

        values = []
        async for value in iter_activity_sse(
            request,
            service,
            after=None,
            sleep=sleep,
            clock=clock,
        ):
            values.append(value)
        return values

    values = asyncio.run(collect())

    assert len(values) == 2
    assert b"event: activity" in values[0]
    assert f"id: {encode_activity_cursor(_cursor('event-1'))}".encode() in values[0]
    assert b'"event_code":"TASK_CREATED"' in values[0]
    assert f"id: {encode_activity_cursor(_cursor('event-2'))}".encode() in values[1]


def test_sse_uses_bounded_batches_and_no_domain_mutation() -> None:
    async def collect() -> None:
        request = FakeRequest(disconnect_after=1)
        service = FakeStreamService()

        async def sleep(_: float) -> None:
            return None

        def clock() -> datetime:
            return datetime(2026, 9, 18, tzinfo=timezone.utc)

        async for _ in iter_activity_sse(
            request, service, after=_cursor("cursor-0"), sleep=sleep, clock=clock
        ):
            raise AssertionError("disconnected stream must not poll")

    asyncio.run(collect())


def test_sse_emits_heartbeat_after_fifteen_seconds() -> None:
    async def collect() -> list[bytes]:
        request = FakeRequest(disconnect_after=3)
        service = FakeStreamService()
        current = [datetime(2026, 9, 18, tzinfo=timezone.utc)]

        async def sleep(_: float) -> None:
            current[0] = current[0].replace(second=15)

        def clock() -> datetime:
            return current[0]

        values = []
        async for value in iter_activity_sse(
            request,
            service,
            after=None,
            sleep=sleep,
            clock=clock,
        ):
            values.append(value)
        return values

    values = asyncio.run(collect())

    assert any(value == b": keep-alive\n\n" for value in values)
