from __future__ import annotations

from pathlib import Path

from scripts.phase9.qualify_clean_setup import (
    CleanSetupReport,
    ProbeResult,
    collect_clean_setup,
)


class FakeSetupProbe:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]) -> ProbeResult:
        self.commands.append(command)
        return ProbeResult(returncode=0, stdout="ok", stderr="")


def test_clean_setup_uses_explicit_commands_and_is_bounded(tmp_path: Path) -> None:
    probe = FakeSetupProbe()
    report = collect_clean_setup(tmp_path, probe=probe)
    assert isinstance(report, CleanSetupReport)
    assert report.uv_lock_ok is True
    assert report.frontend_lock_ok is True
    assert report.external_calls == 0
    assert report.created_paths == ()
    assert all(";" not in part and "&&" not in part for command in probe.commands for part in command)
    assert ("python", "--version") in probe.commands
    assert ("uv", "lock", "--check") in probe.commands
    assert ("npm", "ci", "--ignore-scripts", "--dry-run") in probe.commands


def test_clean_setup_reports_failed_probe_without_secret_output(tmp_path: Path) -> None:
    class FailingProbe(FakeSetupProbe):
        def run(self, command: tuple[str, ...]) -> ProbeResult:
            self.commands.append(command)
            return ProbeResult(returncode=1, stdout="", stderr="credential=redacted")

    report = collect_clean_setup(tmp_path, probe=FailingProbe())
    assert report.uv_lock_ok is False
    assert report.frontend_lock_ok is False
    assert "credential" not in report.to_dict()
