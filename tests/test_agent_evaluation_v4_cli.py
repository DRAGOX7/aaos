from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, cast

import pytest

from aaos.agent.evaluation.v4_cli import (
    V4CliUsageError,
    V4Command,
    parse_v4_command,
    resolve_evaluation_home,
    run_isolated_process,
    run_phase5_v4_command,
)


class _SyntheticChild:
    def __init__(self, stdout: bytes, stderr: bytes, exit_code: int) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code

    def kill(self) -> None:
        self._exit_code = -9


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


def test_worker_exit_error_is_closed_and_uses_parent_interpreter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SECRET_V4_CASE_DO_NOT_LEAK"
    repository = tmp_path / "repo"
    local_appdata = tmp_path / "local-appdata"
    repository.mkdir()
    local_appdata.mkdir()
    launched: dict[str, object] = {}

    def process_factory(command: list[str], **options: object) -> _SyntheticChild:
        launched["command"] = command
        launched["options"] = options
        return _SyntheticChild(secret.encode(), secret.encode(), 7)

    result = run_phase5_v4_command(
        ["manifest"],
        repo_root=repository,
        local_appdata=local_appdata,
        process_factory=cast(Callable[..., subprocess.Popen[bytes]], process_factory),
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "V4 evaluation failed safely",
        "code": "V4_WORKER_EXIT_FAILURE",
    }
    assert secret not in captured.out + captured.err
    command = launched["command"]
    assert isinstance(command, list)
    assert command[:4] == [
        sys.executable,
        "-m",
        "aaos.agent.evaluation.v4_worker",
        "manifest",
    ]
    options = launched["options"]
    assert isinstance(options, dict)
    assert options["cwd"] == str(repository)
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


def test_worker_start_exception_is_sanitized_and_not_propagated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SECRET_V4_CASE_DO_NOT_LEAK"
    repository = tmp_path / "repo"
    local_appdata = tmp_path / "local-appdata"
    repository.mkdir()
    local_appdata.mkdir()

    def process_factory(*args: object, **kwargs: object) -> _SyntheticChild:
        raise RuntimeError(secret)

    result = run_phase5_v4_command(
        ["manifest"],
        repo_root=repository,
        local_appdata=local_appdata,
        process_factory=cast(Callable[..., subprocess.Popen[bytes]], process_factory),
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "V4 evaluation failed safely",
        "code": "V4_WORKER_START_FAILURE",
    }
    assert secret not in captured.out + captured.err
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


def test_malformed_worker_protocol_is_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SECRET_V4_CASE_DO_NOT_LEAK"
    repository = tmp_path / "repo"
    local_appdata = tmp_path / "local-appdata"
    repository.mkdir()
    local_appdata.mkdir()

    def process_factory(*args: object, **kwargs: object) -> _SyntheticChild:
        return _SyntheticChild(secret.encode(), secret.encode(), 0)

    result = run_phase5_v4_command(
        ["manifest"],
        repo_root=repository,
        local_appdata=local_appdata,
        process_factory=cast(Callable[..., subprocess.Popen[bytes]], process_factory),
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "V4 evaluation failed safely",
        "code": "V4_WORKER_PROTOCOL_FAILURE",
    }
    assert secret not in captured.out + captured.err
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()
