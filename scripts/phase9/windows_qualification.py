"""Side-effect-bounded Windows and local-runtime qualification helpers."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class QualificationResult:
    returncode: int
    stdout: str
    stderr: str


class QualificationRunner:
    def run(self, command: tuple[str, ...]) -> QualificationResult:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class WindowsQualificationReport:
    crlf_lf_ok: bool
    path_containment_ok: bool
    symlink_handling_ok: bool
    subprocess_utf8_ok: bool
    cleanup_ok: bool
    node_npm_ok: bool
    browser_startup_ok: bool
    external_calls: int
    credentials_used: bool


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        return False
    return True


def _all_success(results: Iterable[QualificationResult]) -> bool:
    return all(item.returncode == 0 for item in results)


def collect_windows_qualification(
    *, runner: QualificationRunner, root: Path
) -> WindowsQualificationReport:
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    root.mkdir(parents=True, exist_ok=True)
    probe = root / "phase9-windows-probe"
    if probe.exists():
        raise FileExistsError("qualification probe path must be new")
    probe.mkdir()
    try:
        newline_file = probe / "newlines.txt"
        newline_file.write_bytes(b"line-one\r\nline-two\n")
        newline_bytes = newline_file.read_bytes()
        crlf_lf_ok = newline_bytes == b"line-one\r\nline-two\n"
        path_containment_ok = _within(root, newline_file) and not _within(
            root, root.parent / "outside"
        )
        symlink_handling_ok = not probe.is_symlink() and not newline_file.is_symlink()
        commands = (
            (
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding=[Text.UTF8Encoding]::new()",
            ),
            ("node", "--version"),
            ("npm", "--version"),
            ("docker", "version", "--format", "{{.Server.Os}}"),
            ("node", "--version"),
        )
        results = tuple(runner.run(command) for command in commands)
        subprocess_utf8_ok = (
            results[0].returncode == 0 and "credential" not in results[0].stdout.lower()
        )
        node_npm_ok = _all_success(results[1:3])
        browser_startup_ok = results[4].returncode == 0
    finally:
        shutil.rmtree(probe, ignore_errors=False)
    return WindowsQualificationReport(
        crlf_lf_ok=crlf_lf_ok,
        path_containment_ok=path_containment_ok,
        symlink_handling_ok=symlink_handling_ok,
        subprocess_utf8_ok=subprocess_utf8_ok,
        cleanup_ok=not probe.exists(),
        node_npm_ok=node_npm_ok,
        browser_startup_ok=browser_startup_ok,
        external_calls=0,
        credentials_used=False,
    )


__all__ = [
    "QualificationResult",
    "QualificationRunner",
    "WindowsQualificationReport",
    "collect_windows_qualification",
]
