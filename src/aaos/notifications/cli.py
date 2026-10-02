"""`aaos notify …` and `aaos monitor run-once` (Phase 10 spec §4, §6.2, §8).

Output is fixed lines without secrets. The bot token is read with a hidden
prompt during linking and otherwise only from the credential file.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.notifications.adapter import NotificationAdapter
from aaos.notifications.pipeline import NotificationPipeline
from aaos.notifications.telegram import (
    TelegramCredentials,
    TelegramLinker,
    TelegramNotifier,
    TelegramUnavailable,
    is_valid_token,
    load_credentials,
    save_credentials,
)

AMMAN = ZoneInfo("Asia/Amman")
ACTIVE_FROM_HOUR = 7  # 07:00–24:00 Asia/Amman (owner default, spec §5)
AUTH_STAGES = frozenset({"AUTH_REQUIRED", "AUTH_TIMEOUT"})
LINKED_MESSAGE = (
    "<b>AAOS — Telegram linked</b>\n\nYou will get a message here when your Moodle courses change."
)
TEST_MESSAGE = "<b>AAOS — Test message</b>\n\nNotifications are reaching this chat."


def credential_path() -> Path:
    configured = os.environ.get("AAOS_TELEGRAM_CREDENTIAL_PATH")
    return Path(configured) if configured else Path.cwd() / "data" / "credentials" / "telegram.json"


def _environment() -> tuple[str, str, sessionmaker[Session]]:
    """The configured Moodle source, its origin and the normal database (run from aaos-data)."""
    from aaos.moodle.live_sync import _create_existing_database_engine, _normal_database_path
    from aaos.moodle.receiver import load_runtime_moodle_environment
    from aaos.persistence.database import create_session_factory

    environment = load_runtime_moodle_environment()
    engine = _create_existing_database_engine(_normal_database_path())
    return (
        environment.settings.source_system,
        environment.settings.base_origin,
        create_session_factory(engine),
    )


def within_active_hours(now: datetime) -> bool:
    return now.astimezone(AMMAN).hour >= ACTIVE_FROM_HOUR


def deliver_pending(
    pipeline: NotificationPipeline,
    origin: str,
    adapter_factory: Callable[[], NotificationAdapter],
) -> None:
    try:
        adapter = adapter_factory()
    except TelegramUnavailable:
        print("NOTIFY_NOT_LINKED")  # deliveries stay pending until linked
        return
    counts = pipeline.deliver(adapter, origin=origin)
    print(f"NOTIFICATIONS_SENT={counts.sent}")
    print(f"NOTIFICATIONS_SUPPRESSED={counts.suppressed}")
    print(f"NOTIFICATIONS_RETRYING={counts.retrying}")
    print(f"NOTIFICATIONS_FAILED={counts.failed}")


def _telegram() -> TelegramNotifier:
    return TelegramNotifier(load_credentials(credential_path()))


def run_monitor_once(*, force: bool = False) -> int:
    """Sync once, turn complete changes into events, deliver them (the scheduler's entry)."""
    if not force and not within_active_hours(datetime.now(timezone.utc)):
        print("MONITOR_SKIPPED=OUTSIDE_ACTIVE_HOURS")
        return 0
    from aaos.moodle.live_sync import run_configured_moodle_sync_outcome

    outcome = run_configured_moodle_sync_outcome()
    try:
        source, origin, sessions = _environment()
    except Exception:
        print("MONITOR_FAILED=CONFIGURATION")
        return 1
    pipeline = NotificationPipeline(sessions)
    if outcome.exit_code == 0:
        counts = pipeline.process_snapshots(source)
        print(f"EVENTS_CREATED={counts.events_created}")
        print(f"DUPLICATES_SKIPPED={counts.duplicates_skipped}")
    elif outcome.browser_stage in AUTH_STAGES:
        alerted = pipeline.record_auth_required(source)
        print("MONITOR_AUTH_REQUIRED=" + ("ALERT_QUEUED" if alerted else "ALREADY_ALERTED"))
    deliver_pending(pipeline, origin, _telegram)
    return outcome.exit_code


def _link() -> int:
    token = getpass.getpass("Telegram bot token (hidden): ").strip()
    if not is_valid_token(token):
        print("NOTIFY_LINK_FAILED=TOKEN_FORMAT")
        return 1
    try:
        linker = TelegramLinker(token)
        username = linker.bot_username()
        after = linker.latest_update_id()
        input(f"Open Telegram, send /start to @{username}, then press Enter here... ")
        chat_id = linker.chat_after(after_update_id=after)
        credentials = TelegramCredentials(token, chat_id)
        save_credentials(credential_path(), credentials)
    except TelegramUnavailable:
        print("NOTIFY_LINK_FAILED=TELEGRAM")
        return 1
    result = TelegramNotifier(credentials).send(LINKED_MESSAGE)
    print("NOTIFY_LINKED" if result.sent else "NOTIFY_LINKED_BUT_TEST_FAILED")
    print("Next: run `aaos notify enable` to start notifications.")
    return 0 if result.sent else 1


def _status(source: str, sessions: sessionmaker[Session]) -> int:
    from aaos.persistence.notification_models import (
        NotificationDeliveryORM,
        NotificationStateORM,
    )

    try:
        load_credentials(credential_path())
        print("TELEGRAM=LINKED")
    except TelegramUnavailable:
        print("TELEGRAM=NOT_LINKED")
    with sessions() as session:
        state = session.get(NotificationStateORM, source)
        enabled = state is not None and state.enabled_at is not None and not state.muted
        print("NOTIFICATIONS=" + ("ENABLED" if enabled else "DISABLED"))
        for status in ("PENDING", "SENT", "FAILED", "SUPPRESSED"):
            count = session.scalar(
                select(func.count()).where(NotificationDeliveryORM.status == status)
            )
            print(f"DELIVERIES_{status}={count}")
    return 0


def run_notify_command(argv: Sequence[str]) -> int:
    command = list(argv)
    if command == ["link"]:
        return _link()
    if command == ["unlink"]:
        path = credential_path()
        if path.is_file():
            path.unlink()
        try:
            source, _origin, sessions = _environment()
            NotificationPipeline(sessions).mute(source)
        except Exception:
            pass
        print("NOTIFY_UNLINKED")
        return 0
    if command == ["test"]:
        try:
            result = _telegram().send(TEST_MESSAGE)
        except TelegramUnavailable:
            print("NOTIFY_NOT_LINKED")
            return 1
        print("NOTIFY_TEST_SENT" if result.sent else f"NOTIFY_TEST_FAILED={result.error}")
        return 0 if result.sent else 1
    if command in (["enable"], ["disable"], ["status"]):
        try:
            source, _origin, sessions = _environment()
        except Exception:
            print("NOTIFY_FAILED=CONFIGURATION")
            return 1
        if command == ["status"]:
            return _status(source, sessions)
        pipeline = NotificationPipeline(sessions)
        if command == ["enable"]:
            baseline = pipeline.enable(source)
            print("NOTIFY_ENABLED")
            print(f"BASELINE_SNAPSHOT={baseline if baseline is not None else 'NEXT_COMPLETE_SYNC'}")
        else:
            pipeline.mute(source)
            print("NOTIFY_DISABLED")
        return 0
    print("usage: aaos notify link|enable|disable|status|test|unlink")
    return 2


def run_monitor_command(argv: Sequence[str]) -> int:
    if list(argv) == ["run-once"]:
        return run_monitor_once()
    if list(argv) == ["run-once", "--now"]:
        return run_monitor_once(force=True)  # "Sync Now": ignores active hours
    print("usage: aaos monitor run-once [--now]")
    return 2
