from __future__ import annotations

from pathlib import Path

from scripts.phase9.windows_qualification import (
    QualificationResult,
    QualificationRunner,
    collect_windows_qualification,
)


class FakeRunner(QualificationRunner):
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]) -> QualificationResult:
        self.commands.append(command)
        return QualificationResult(returncode=0, stdout="ok", stderr="")


def test_windows_qualification_is_bounded_and_cleans_temp_paths(tmp_path: Path) -> None:
    runner = FakeRunner()
    report = collect_windows_qualification(runner=runner, root=tmp_path)
    assert report.crlf_lf_ok is True
    assert report.path_containment_ok is True
    assert report.symlink_handling_ok is True
    assert report.subprocess_utf8_ok is True
    assert report.cleanup_ok is True
    assert report.node_npm_ok is True
    assert report.browser_startup_ok is True
    assert report.external_calls == 0
    assert report.credentials_used is False
    assert not list(tmp_path.iterdir())
    assert ("node", "--version") in runner.commands
    assert ("npm", "--version") in runner.commands


def test_windows_qualification_never_uses_a_shell_string(tmp_path: Path) -> None:
    runner = FakeRunner()
    collect_windows_qualification(runner=runner, root=tmp_path)
    assert all(
        all("&&" not in part and ";" not in part for part in command) for command in runner.commands
    )
