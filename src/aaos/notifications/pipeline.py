"""Phase 10 pipeline: snapshot pairs → events → outbox → one adapter (spec §4–5).

Deterministic and model-free. Only COMPLETE snapshots are read; the cursor
advances in the same transaction that stores the events, and a unique event
key plus one delivery per (event, channel) keep every change to one send.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.moodle.changes import deserialize_state
from aaos.notifications.adapter import (
    PERMANENT_ERRORS,
    DeliveryError,
    NotificationAdapter,
)
from aaos.notifications.events import (
    NotificationEvent,
    NotificationKind,
    derive_events,
    event_key,
)
from aaos.notifications.messages import render, render_digest
from aaos.persistence.moodle_sync_models import MoodleSyncSnapshotORM
from aaos.persistence.notification_models import (
    NotificationDeliveryORM,
    NotificationEventORM,
    NotificationStateORM,
)

CHANNEL = "TELEGRAM"
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 900, 3600, 14400)
DEFAULT_DIGEST_THRESHOLD = 5


@dataclass(frozen=True, slots=True)
class RunCounts:
    events_created: int = 0
    duplicates_skipped: int = 0


@dataclass(frozen=True, slots=True)
class DeliveryCounts:
    sent: int = 0
    failed: int = 0
    retrying: int = 0
    suppressed: int = 0


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class NotificationPipeline:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        digest_threshold: int = DEFAULT_DIGEST_THRESHOLD,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._digest_threshold = digest_threshold
        self._pause = pause

    # --- state -----------------------------------------------------------

    def enable(self, source_system: str) -> int | None:
        """Start notifying from now: the latest COMPLETE snapshot is the baseline."""
        with self._sessions.begin() as session:
            baseline = self._latest_complete_id(session, source_system)
            state = session.get(NotificationStateORM, source_system)
            if state is None:
                state = NotificationStateORM(source_system=source_system)
                session.add(state)
            state.enabled_at = _iso(self._clock())
            state.baseline_snapshot_id = baseline
            state.last_processed_snapshot_id = baseline
            state.auth_alert_after_snapshot_id = None
            state.muted = False
            return baseline

    def mute(self, source_system: str) -> None:
        with self._sessions.begin() as session:
            state = session.get(NotificationStateORM, source_system)
            if state is not None:
                state.muted = True

    def _active_state(self, session: Session, source_system: str) -> NotificationStateORM | None:
        state = session.get(NotificationStateORM, source_system)
        if state is None or state.muted or state.enabled_at is None:
            return None
        return state

    @staticmethod
    def _latest_complete_id(session: Session, source_system: str) -> int | None:
        return session.scalar(
            select(MoodleSyncSnapshotORM.id)
            .where(
                MoodleSyncSnapshotORM.source_system == source_system,
                MoodleSyncSnapshotORM.status == "COMPLETE",
            )
            .order_by(MoodleSyncSnapshotORM.id.desc())
            .limit(1)
        )

    # --- events ----------------------------------------------------------

    def process_snapshots(self, source_system: str) -> RunCounts:
        """Turn every new COMPLETE→COMPLETE pair after the cursor into stored events."""
        created = duplicates = 0
        with self._sessions.begin() as session:
            state = self._active_state(session, source_system)
            if state is None:
                return RunCounts()
            rows = session.scalars(
                select(MoodleSyncSnapshotORM)
                .where(
                    MoodleSyncSnapshotORM.source_system == source_system,
                    MoodleSyncSnapshotORM.status == "COMPLETE",
                    MoodleSyncSnapshotORM.id >= (state.last_processed_snapshot_id or 0),
                )
                .order_by(MoodleSyncSnapshotORM.id)
            ).all()
            if state.last_processed_snapshot_id is None:
                # Enabled before any complete sync: the first one is the baseline.
                if rows:
                    state.baseline_snapshot_id = state.last_processed_snapshot_id = rows[0].id
                return RunCounts()
            previous = next(
                (row for row in rows if row.id == state.last_processed_snapshot_id), None
            )
            if previous is None:
                return RunCounts()
            for current in rows:
                if current.id <= previous.id:
                    continue
                events = derive_events(
                    deserialize_state(previous.snapshot_state),
                    deserialize_state(current.snapshot_state),
                    from_snapshot_id=previous.id,
                    to_snapshot_id=current.id,
                    detected_at=self._clock(),
                )
                for event in events:
                    if self._store(session, event):
                        created += 1
                    else:
                        duplicates += 1
                state.last_processed_snapshot_id = current.id
                previous = current
        return RunCounts(created, duplicates)

    def record_auth_required(self, source_system: str) -> bool:
        """One "login needed" event per expiry episode (since the last complete sync)."""
        with self._sessions.begin() as session:
            state = self._active_state(session, source_system)
            if state is None:
                return False
            last_complete = self._latest_complete_id(session, source_system) or 0
            if state.auth_alert_after_snapshot_id == last_complete:
                return False
            event = NotificationEvent(
                kind=NotificationKind.AUTH_REQUIRED,
                event_key=event_key(
                    NotificationKind.AUTH_REQUIRED, source_system, str(last_complete)
                ),
                source_system=source_system,
                course_source_id=None,
                entity_source_id=None,
                from_snapshot_id=None,
                to_snapshot_id=last_complete or None,
                payload={},
            )
            state.auth_alert_after_snapshot_id = last_complete
            return self._store(session, event)

    def _store(self, session: Session, event: NotificationEvent) -> bool:
        exists = session.scalar(
            select(NotificationEventORM.id).where(NotificationEventORM.event_key == event.event_key)
        )
        if exists is not None:
            return False
        row = NotificationEventORM(
            event_key=event.event_key,
            kind=event.kind.value,
            source_system=event.source_system,
            course_source_id=event.course_source_id,
            entity_source_id=event.entity_source_id,
            from_snapshot_id=event.from_snapshot_id,
            to_snapshot_id=event.to_snapshot_id,
            payload=dict(event.payload),
            created_at=_iso(self._clock()),
        )
        session.add(row)
        session.flush()
        session.add(
            NotificationDeliveryORM(
                event_id=row.id,
                channel=CHANNEL,
                status="PENDING",
                attempts=0,
                next_attempt_at=_iso(self._clock()),
            )
        )
        return True

    # --- delivery --------------------------------------------------------

    def deliver(self, adapter: NotificationAdapter, *, origin: str) -> DeliveryCounts:
        """Send due deliveries: one digest above the threshold, else one message each."""
        now = self._clock()
        with self._sessions() as session:
            due = session.execute(
                select(NotificationDeliveryORM, NotificationEventORM)
                .join(
                    NotificationEventORM,
                    NotificationEventORM.id == NotificationDeliveryORM.event_id,
                )
                .where(
                    NotificationDeliveryORM.status == "PENDING",
                    NotificationDeliveryORM.next_attempt_at <= _iso(now),
                )
                .order_by(NotificationEventORM.id)
            ).all()
            batch = [
                (delivery.id, NotificationKind(event.kind), dict(event.payload))
                for delivery, event in due
            ]
        if not batch:
            return DeliveryCounts()
        if len(batch) > self._digest_threshold:
            result = adapter.send(
                render_digest([(kind, payload) for _id, kind, payload in batch], origin=origin)
            )
            return self._settle([delivery_id for delivery_id, _k, _p in batch], result, digest=True)
        totals = DeliveryCounts()
        for index, (delivery_id, kind, payload) in enumerate(batch):
            if index:
                self._pause(1.0)  # Telegram: at most one message per second to a chat
            result = adapter.send(render(kind, payload, origin=origin))
            counts = self._settle([delivery_id], result, digest=False)
            totals = DeliveryCounts(
                totals.sent + counts.sent,
                totals.failed + counts.failed,
                totals.retrying + counts.retrying,
                totals.suppressed + counts.suppressed,
            )
            if result.error is DeliveryError.RATE_LIMITED:
                break  # the rest stay pending for the next run
        return totals

    def _settle(self, delivery_ids: list[int], result: object, *, digest: bool) -> DeliveryCounts:
        from aaos.notifications.adapter import DeliveryResult

        assert isinstance(result, DeliveryResult)
        now = self._clock()
        sent = failed = retrying = suppressed = 0
        with self._sessions.begin() as session:
            for position, delivery_id in enumerate(delivery_ids):
                delivery = session.get(NotificationDeliveryORM, delivery_id)
                if delivery is None or delivery.status != "PENDING":
                    continue
                if result.sent:
                    if digest and position:
                        # Covered by the digest message sent for the first one.
                        delivery.status = "SUPPRESSED"
                        suppressed += 1
                    else:
                        delivery.status = "SENT"
                        sent += 1
                    delivery.sent_at = _iso(now)
                    delivery.last_error_code = None
                    continue
                delivery.attempts += 1
                delivery.last_error_code = (result.error or DeliveryError.PROVIDER_ERROR).value
                if result.error in PERMANENT_ERRORS or delivery.attempts >= MAX_ATTEMPTS:
                    delivery.status = "FAILED"
                    delivery.next_attempt_at = None
                    failed += 1
                else:
                    wait = result.retry_after_seconds or BACKOFF_SECONDS[delivery.attempts - 1]
                    delivery.next_attempt_at = _iso(now + timedelta(seconds=wait))
                    retrying += 1
        return DeliveryCounts(sent, failed, retrying, suppressed)
