"""Canonical `aaos phase5 v4` command surface with child-process output isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Callable, Sequence

from aaos.agent.evaluation.v4_ledger import HeldoutLedgerError, V4HeldoutLedger
from aaos.agent.evaluation.v4_runner import (
    AGGREGATE_SCHEMA_VERSION,
    MANIFEST_VERIFIER_ID,
    RUNNER_VERSION,
    SCORER_ID,
    V4SafeAggregate,
)


class V4Command(StrEnum):
    MANIFEST = "manifest"
    CALIBRATION = "calibration"
    HELDOUT = "heldout"
    LEDGER_STATUS = "ledger-status"


class V4SafeErrorCode(StrEnum):
    """Closed, non-semantic error codes for the public V4 command surface."""

    USAGE_ERROR = "V4_USAGE_ERROR"
    CLI_FAILURE = "V4_CLI_FAILURE"
    LEDGER_FAILURE = "V4_LEDGER_FAILURE"
    WORKER_START_FAILURE = "V4_WORKER_START_FAILURE"
    WORKER_EXIT_FAILURE = "V4_WORKER_EXIT_FAILURE"
    WORKER_PROTOCOL_FAILURE = "V4_WORKER_PROTOCOL_FAILURE"
    MANIFEST_STRUCTURE_FAILURE = "V4_MANIFEST_STRUCTURE_FAILURE"
    REPORT_WRITE_FAILURE = "V4_REPORT_WRITE_FAILURE"


class V4WorkerExitCode(IntEnum):
    """Private closed status codes used to classify safe worker failures."""

    MANIFEST_STRUCTURE_FAILURE = 10
    REPORT_WRITE_FAILURE = 11
    PROTOCOL_FAILURE = 12


class V4CliUsageError(ValueError):
    """The V4 command surface was not invoked exactly."""


class V4CliError(RuntimeError):
    """A fixed, safe CLI failure code."""

    def __init__(self, code: V4SafeErrorCode = V4SafeErrorCode.CLI_FAILURE) -> None:
        self.code = code
        super().__init__(code.value)


def parse_v4_command(argv: Sequence[str]) -> V4Command:
    if len(argv) != 1:
        raise V4CliUsageError("one V4 mode is required")
    try:
        return V4Command(argv[0])
    except (TypeError, ValueError):
        raise V4CliUsageError("V4 mode is unsupported") from None


def resolve_evaluation_home(
    local_appdata: Path | str | None,
    *,
    repo_root: Path | str,
) -> Path:
    raw_appdata = local_appdata
    if raw_appdata is None:
        raw_appdata = os.environ.get("LOCALAPPDATA")
    if raw_appdata is None:
        raise V4CliError
    base_candidate = Path(raw_appdata)
    if not base_candidate.is_absolute():
        raise V4CliError
    try:
        base = base_candidate.resolve(strict=True)
        repository = Path(repo_root).resolve(strict=True)
        home = (base / "AAOS" / "evaluation" / "v4").resolve(strict=False)
        if not home.is_relative_to(base):
            raise ValueError
        if home == repository or home.is_relative_to(repository):
            raise ValueError
        if repository.is_relative_to(home):
            raise ValueError
        return home
    except Exception:
        raise V4CliError from None


def run_isolated_process(
    command: Sequence[str],
    *,
    cwd: Path | str,
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    """Run a worker with bounded-memory stdout/stderr drains and no visible output."""

    options: dict[str, object] = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = process_factory(list(command), **options)
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise V4CliError(V4SafeErrorCode.WORKER_START_FAILURE)

    readers = [
        threading.Thread(target=_drain_pipe, args=(process.stdout,), daemon=True),
        threading.Thread(target=_drain_pipe, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    return_code = process.wait()
    for reader in readers:
        reader.join()
    return int(return_code)


def run_phase5_v4_command(
    argv: Sequence[str],
    *,
    repo_root: Path | str | None = None,
    local_appdata: Path | str | None = None,
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    try:
        command = parse_v4_command(argv)
    except V4CliUsageError:
        _write_error(2, V4SafeErrorCode.USAGE_ERROR)
        return 2

    repository = Path(repo_root).resolve() if repo_root is not None else _repository_root()
    try:
        home = resolve_evaluation_home(local_appdata, repo_root=repository)
        if command is V4Command.LEDGER_STATUS:
            return _write_ledger_status(home)
        return _run_worker(command, repository, home, process_factory)
    except HeldoutLedgerError:
        _write_error(1, V4SafeErrorCode.LEDGER_FAILURE)
        return 1
    except V4CliError as exc:
        _write_error(1, exc.code)
        return 1
    except Exception:
        _write_error(1, V4SafeErrorCode.CLI_FAILURE)
        return 1


def _run_worker(
    command: V4Command,
    repository: Path,
    home: Path,
    process_factory: Callable[..., subprocess.Popen[bytes]],
) -> int:
    ipc_root = home / "ipc"
    if ipc_root.is_symlink():
        raise V4CliError
    ipc_root.mkdir(parents=True, exist_ok=True)
    if not ipc_root.resolve().is_relative_to(home):
        raise V4CliError
    response_path = ipc_root / f"result-{uuid.uuid4().hex}.json"
    worker = [
        sys.executable,
        "-m",
        "aaos.agent.evaluation.v4_worker",
        command.value,
        str(response_path),
        str(repository),
    ]
    try:
        try:
            exit_code = run_isolated_process(
                worker, cwd=repository, process_factory=process_factory
            )
        except Exception:
            _write_error(1, V4SafeErrorCode.WORKER_START_FAILURE)
            return 1

        if exit_code == 4:
            _write_error(4, V4SafeErrorCode.LEDGER_FAILURE)
            return 4
        worker_errors = {
            V4WorkerExitCode.MANIFEST_STRUCTURE_FAILURE: (
                V4SafeErrorCode.MANIFEST_STRUCTURE_FAILURE
            ),
            V4WorkerExitCode.REPORT_WRITE_FAILURE: V4SafeErrorCode.REPORT_WRITE_FAILURE,
            V4WorkerExitCode.PROTOCOL_FAILURE: V4SafeErrorCode.WORKER_PROTOCOL_FAILURE,
        }
        try:
            worker_code = V4WorkerExitCode(exit_code)
        except ValueError:
            worker_code = None
        if worker_code in worker_errors:
            _write_error(1, worker_errors[worker_code])
            return 1
        if exit_code not in {0, 3}:
            _write_error(1, V4SafeErrorCode.WORKER_EXIT_FAILURE)
            return 1
        try:
            aggregate = V4SafeAggregate.from_dict(_read_bounded_json(response_path))
            public_json = aggregate.to_json()
            if len(public_json) > 16_384:
                raise ValueError
        except Exception:
            _write_error(1, V4SafeErrorCode.WORKER_PROTOCOL_FAILURE)
            return 1
        sys.stdout.write(public_json + "\n")
        sys.stdout.flush()
        return exit_code
    finally:
        try:
            response_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_ledger_status(home: Path) -> int:
    status = V4HeldoutLedger(home / "heldout-ledger.json").status()
    payload = {
        "aggregate_schema_identity": AGGREGATE_SCHEMA_VERSION,
        "candidate_sha": status.candidate_sha,
        "fake_only_configuration": {
            "external_writes_enabled": False,
            "live_rag_enabled": False,
        },
        "ledger": status.to_dict(),
        "manifest_verifier_identity": MANIFEST_VERIFIER_ID,
        "runner_identity": RUNNER_VERSION,
        "scorer_identity": SCORER_ID,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 4096:
        _write_error(1, V4SafeErrorCode.CLI_FAILURE)
        return 1
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()
    return 0


def _read_bounded_json(path: Path) -> object:
    try:
        if path.is_symlink() or path.stat().st_size > 32_768:
            raise ValueError
        raw = path.read_bytes()
        return json.loads(raw)
    except Exception:
        raise V4CliError(V4SafeErrorCode.WORKER_PROTOCOL_FAILURE) from None


def _write_error(exit_code: int, code: V4SafeErrorCode) -> None:
    messages = {
        V4SafeErrorCode.USAGE_ERROR: "usage: aaos phase5 v4 {manifest|calibration|heldout|ledger-status}",
        V4SafeErrorCode.LEDGER_FAILURE: "ledger is corrupt or unavailable; failing closed",
    }
    payload = {"error": messages.get(code, "V4 evaluation failed safely")}
    if code is not V4SafeErrorCode.USAGE_ERROR:
        payload["code"] = code.value
    sys.stderr.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _drain_pipe(pipe: object) -> None:
    read = getattr(pipe, "read", None)
    if not callable(read):
        return
    while read(4096):
        pass
