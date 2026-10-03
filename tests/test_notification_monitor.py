"""Phase 10 slices 10E–10F: the monitor entry point and its decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aaos.moodle.live_sync import SyncOutcome
from aaos.notifications import cli
from aaos.notifications.adapter import DeliveryResult
from aaos.notifications.telegram import TelegramUnavailable


@pytest.mark.parametrize(
    ("utc_hour", "active"),
    [(3, False), (4, True), (12, True), (20, True), (21, False), (22, False), (23, False)],
)
def test_active_hours_are_07_to_24_in_amman(utc_hour: int, active: bool) -> None:
    # Amman is UTC+3: 04:00 UTC is 07:00 local, 20:59 UTC is 23:59, 21:00 UTC is midnight.
    assert cli.within_active_hours(datetime(2026, 10, 2, utc_hour, tzinfo=timezone.utc)) is active


def test_outside_active_hours_no_sync_runs(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cli, "within_active_hours", lambda _now: False)
    monkeypatch.setattr(
        "aaos.moodle.live_sync.run_configured_moodle_sync_outcome",
        lambda **_kwargs: pytest.fail("Moodle must not be contacted outside active hours"),
    )

    assert cli.run_monitor_once() == 0
    assert "MONITOR_SKIPPED=OUTSIDE_ACTIVE_HOURS" in capsys.readouterr().out


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_snapshots(self, _source: str):  # type: ignore[no-untyped-def]
        from aaos.notifications.pipeline import RunCounts

        self.calls.append("process")
        return RunCounts(1, 0)

    def record_auth_required(self, _source: str) -> bool:
        self.calls.append("auth")
        return True

    def deliver(self, _adapter, *, origin: str):  # type: ignore[no-untyped-def]
        from aaos.notifications.pipeline import DeliveryCounts

        self.calls.append("deliver")
        return DeliveryCounts(sent=1)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (SyncOutcome(0), ["process", "deliver"]),
        (SyncOutcome(1, browser_stage="AUTH_REQUIRED"), ["auth", "deliver"]),
        (SyncOutcome(1, browser_stage="AUTH_TIMEOUT"), ["auth", "deliver"]),
        (SyncOutcome(1, browser_stage="CLEANUP"), ["deliver"]),
        (SyncOutcome(1), ["deliver"]),
    ],
)
def test_the_sync_outcome_decides_what_is_recorded(monkeypatch, outcome, expected) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder()
    monkeypatch.setattr(
        "aaos.moodle.live_sync.run_configured_moodle_sync_outcome", lambda **_kwargs: outcome
    )
    monkeypatch.setattr(cli, "_environment", lambda: ("src", "https://m.example", None))
    monkeypatch.setattr(cli, "NotificationPipeline", lambda _sessions: recorder)
    monkeypatch.setattr(cli, "_telegram", lambda: object())

    exit_code = cli.run_monitor_once(force=True)

    assert recorder.calls == expected
    assert exit_code == outcome.exit_code


@pytest.mark.parametrize(("force", "unattended"), [(False, True), (True, False)])
def test_only_the_scheduled_run_uses_the_short_unattended_sign_in_wait(
    monkeypatch, force: bool, unattended: bool
) -> None:  # type: ignore[no-untyped-def]
    seen: list[bool] = []

    def sync(*, unattended: bool) -> SyncOutcome:
        seen.append(unattended)
        return SyncOutcome(1)

    monkeypatch.setattr(cli, "within_active_hours", lambda _now: True)
    monkeypatch.setattr("aaos.moodle.live_sync.run_configured_moodle_sync_outcome", sync)
    monkeypatch.setattr(cli, "_environment", lambda: ("src", "https://m.example", None))
    monkeypatch.setattr(cli, "NotificationPipeline", lambda _sessions: Recorder())
    monkeypatch.setattr(cli, "_telegram", lambda: object())

    cli.run_monitor_once(force=force)

    assert seen == [unattended]


def test_unlinked_telegram_leaves_deliveries_pending(capsys) -> None:  # type: ignore[no-untyped-def]
    recorder = Recorder()

    def not_linked():  # type: ignore[no-untyped-def]
        raise TelegramUnavailable()

    cli.deliver_pending(recorder, "https://m.example", not_linked)  # type: ignore[arg-type]

    assert recorder.calls == []
    assert "NOTIFY_NOT_LINKED" in capsys.readouterr().out


def test_the_default_credential_file_is_under_the_runtime_data_root(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AAOS_TELEGRAM_CREDENTIAL_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert cli.credential_path() == tmp_path / "data" / "credentials" / "telegram.json"


def test_the_test_command_never_prints_the_token(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    class Sent:
        def send(self, _message: str) -> DeliveryResult:
            return DeliveryResult(sent=True)

    monkeypatch.setattr(cli, "_telegram", lambda: Sent())

    assert cli.run_notify_command(["test"]) == 0
    assert capsys.readouterr().out.strip() == "NOTIFY_TEST_SENT"
