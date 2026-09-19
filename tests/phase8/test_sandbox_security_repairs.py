from __future__ import annotations

import sys
import time
from typing import Sequence

from aaos.phase8.sandbox import CommandResult, SubprocessRunner


def _flood_script(*, stream: str, size: int, sleep: bool = False) -> tuple[str, ...]:
    statement = f"import sys; sys.{stream}.write('x' * {size}); sys.{stream}.flush()"
    if sleep:
        statement += "; time.sleep(30)"
    return (sys.executable, "-c", statement)


def _run_flood(runner: SubprocessRunner, args: Sequence[str], limit: int = 1024) -> CommandResult:
    return runner.run(
        args,
        input_bytes=b"",
        timeout_seconds=5.0,
        stdout_limit_bytes=limit,
        stderr_limit_bytes=limit,
    )


def test_stdout_flood_is_bounded_while_reading() -> None:
    result = _run_flood(SubprocessRunner(), _flood_script(stream="stdout", size=100_000))
    assert result.output_exceeded is True
    assert len(result.stdout) <= 1024


def test_stderr_flood_is_bounded_while_reading() -> None:
    result = _run_flood(SubprocessRunner(), _flood_script(stream="stderr", size=100_000))
    assert result.output_exceeded is True
    assert len(result.stderr) <= 1024


def test_simultaneous_stdout_and_stderr_flood_does_not_deadlock() -> None:
    script = (
        "import sys; "
        "sys.stdout.write('x' * 100000); sys.stdout.flush(); "
        "sys.stderr.write('y' * 100000); sys.stderr.flush()"
    )
    started = time.monotonic()
    result = _run_flood(SubprocessRunner(), (sys.executable, "-c", script))
    elapsed = time.monotonic() - started
    assert result.output_exceeded is True
    assert len(result.stdout) <= 1024
    assert len(result.stderr) <= 1024
    assert elapsed < 5


def test_output_exactly_at_boundary_is_not_rejected() -> None:
    result = _run_flood(SubprocessRunner(), _flood_script(stream="stdout", size=1024))
    assert result.output_exceeded is False
    assert len(result.stdout) == 1024


def test_output_over_boundary_terminates_running_process() -> None:
    result = _run_flood(
        SubprocessRunner(), _flood_script(stream="stdout", size=100_000, sleep=True)
    )
    assert result.output_exceeded is True
    assert result.returncode != 0
