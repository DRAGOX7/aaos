from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aaos.agent.evaluation.v4_cli import (
    V4CliUsageError,
    V4Command,
    parse_v4_command,
    resolve_evaluation_home,
    run_isolated_process,
)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["manifest"], V4Command.MANIFEST),
        (["calibration"], V4Command.CALIBRATION),
        (["heldout"], V4Command.HELDOUT),
        (["ledger-status"], V4Command.LEDGER_STATUS),
    ],
)
def test_v4_cli_has_one_canonical_command_per_mode(argv: list[str], expected: V4Command) -> None:
    assert parse_v4_command(argv) is expected


def test_v4_cli_rejects_extra_or_unknown_modes() -> None:
    with pytest.raises(V4CliUsageError):
        parse_v4_command(["heldout", "--force"])
    with pytest.raises(V4CliUsageError):
        parse_v4_command(["real"])


def test_local_evaluation_home_is_fixed_under_appdata(tmp_path: Path) -> None:
    appdata = tmp_path / "LocalAppData"
    repo = tmp_path / "repo"
    appdata.mkdir()
    repo.mkdir()

    resolved = resolve_evaluation_home(appdata, repo_root=repo)

    assert resolved == appdata / "AAOS" / "evaluation" / "v4"
    assert repo not in resolved.parents


def test_isolated_child_stdout_stderr_are_drained_and_discarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "SECRET_V4_CASE_DO_NOT_LEAK"
    child = (
        f"import sys; print({marker!r}); print({marker!r}, file=sys.stderr); raise SystemExit(7)"
    )

    exit_code = run_isolated_process(
        [sys.executable, "-c", child],
        cwd=tmp_path,
        process_factory=subprocess.Popen,
    )
    captured = capsys.readouterr()

    assert exit_code == 7
    assert captured.out == ""
    assert captured.err == ""
    assert marker not in captured.out + captured.err


def test_aaos_cli_routes_phase5_v4_to_the_canonical_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    observed: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["aaos", "phase5", "v4", "calibration"])
    monkeypatch.setattr(cli, "run_cli", lambda argv, run_doctor: 97)

    def fake_runner(argv: list[str]) -> int:
        observed.append(list(argv))
        return 0

    monkeypatch.setattr("aaos.agent.evaluation.v4_cli.run_phase5_v4_command", fake_runner)

    assert cli.main() == 0
    assert observed == [["calibration"]]
