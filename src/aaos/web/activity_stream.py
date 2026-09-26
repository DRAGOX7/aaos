"""Read-only Server-Sent Events transport for durable Activity rows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable, Protocol

from aaos.web.activity import ActivityCursor, ActivityEvent, encode_activity_cursor

_BATCH_SIZE = 50
_POLL_INTERVAL_SECONDS = 0.5
_HEARTBEAT_INTERVAL_SECONDS = 15.0


class ActivityReplayUnavailable(RuntimeError):
    """The requested cursor is no longer available for a safe replay."""


class _DisconnectChecker(Protocol):
    async def is_disconnected(self) -> bool: ...


class ActivityStreamService(Protocol):
    def current_high_watermark(self) -> ActivityCursor | None: ...

    def tail_with_cursors(
        self,
        *,
        after: ActivityCursor | None,
        limit: int,
    ) -> tuple[tuple[ActivityEvent, ActivityCursor], ...]: ...


def _event_frame(event: ActivityEvent, cursor: ActivityCursor) -> bytes:
    payload = json.dumps(
        json.loads(event.model_dump_json()),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (f"id: {encode_activity_cursor(cursor)}\nevent: activity\ndata: {payload}\n\n").encode(
        "utf-8"
    )


async def iter_activity_sse(
    request: _DisconnectChecker,
    service: ActivityStreamService,
    *,
    after: ActivityCursor | None,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], datetime],
) -> AsyncIterator[bytes]:
    """Tail durable Activity rows without creating a broker or domain mutation."""

    cursor = after if after is not None else service.current_high_watermark()
    last_heartbeat = clock()
    while True:
        if await request.is_disconnected():
            return

        now = clock()
        if (now - last_heartbeat).total_seconds() >= _HEARTBEAT_INTERVAL_SECONDS:
            yield b": keep-alive\n\n"
            last_heartbeat = now

        try:
            batch = service.tail_with_cursors(after=cursor, limit=_BATCH_SIZE)
        except ActivityReplayUnavailable:
            yield b'event: recovery_required\ndata: {"reason":"REPLAY_REQUIRED"}\n\n'
            return

        for event, event_cursor in batch:
            cursor = event_cursor
            yield _event_frame(event, event_cursor)

        await sleep(_POLL_INTERVAL_SECONDS)


__all__ = ["ActivityReplayUnavailable", "ActivityStreamService", "iter_activity_sse"]
