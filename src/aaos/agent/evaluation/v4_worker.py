"""Private V4 CLI worker. Public output is returned only through a typed IPC file."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from aaos.agent.evaluation.v4 import (
    V4EvaluationManifest,
    build_v4_manifests,
    score_v4_evaluation,
)
from aaos.agent.evaluation.v4_cli import (
    V4Command,
    V4WorkerExitCode,
    resolve_evaluation_home,
)
from aaos.agent.evaluation.v4_ledger import HeldoutLedgerError, V4HeldoutLedger
from aaos.agent.evaluation.v4_live import (
    check_configured_local_model,
    create_configured_local_executor,
)
from aaos.agent.evaluation.v4_runner import (
    V4ExecutionBatch,
    V4RunnerError,
    V4SafeAggregate,
    V4SealedRunner,
)

_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class _ManifestOnlyExecutor:
    def __call__(self, manifest: V4EvaluationManifest) -> V4ExecutionBatch:
        raise RuntimeError("manifest mode cannot execute model cases")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        return 2
    try:
        command = V4Command(arguments[0])
        if command is V4Command.LEDGER_STATUS:
            return 2
        response_path = Path(arguments[1])
        repo_root = Path(arguments[2]).resolve(strict=True)
        home = resolve_evaluation_home(None, repo_root=repo_root)
        _validate_response_path(response_path, home)
        candidate_sha = _candidate_sha(repo_root)
        ledger = V4HeldoutLedger(home / "heldout-ledger.json")
        report_root = home / "reports"
        if command is V4Command.MANIFEST:
            runner = V4SealedRunner(
                manifest_factory=build_v4_manifests,
                scorer=score_v4_evaluation,
                executor=_ManifestOnlyExecutor(),
                candidate_sha=candidate_sha,
                ledger=ledger,
                report_root=report_root,
            )
            try:
                report = runner.verify_manifest()
            except V4RunnerError as exc:
                if exc.code == "manifest_failed":
                    return int(V4WorkerExitCode.MANIFEST_STRUCTURE_FAILURE)
                if exc.code == "report_write_failed":
                    return int(V4WorkerExitCode.REPORT_WRITE_FAILURE)
                return 1
        else:
            executor = create_configured_local_executor()
            runner = V4SealedRunner(
                manifest_factory=build_v4_manifests,
                scorer=score_v4_evaluation,
                executor=executor,
                candidate_sha=candidate_sha,
                ledger=ledger,
                report_root=report_root,
                preflight=check_configured_local_model,
            )
            report = (
                runner.calibrate()
                if command is V4Command.CALIBRATION
                else runner.evaluate_heldout()
            )
        try:
            _write_response(response_path, report)
        except Exception:
            return int(V4WorkerExitCode.PROTOCOL_FAILURE)
        return 0 if report.passed else 3
    except HeldoutLedgerError:
        return 4
    except (V4RunnerError, OSError, ValueError, RuntimeError):
        return 1
    except Exception:
        return 1


def _candidate_sha(repo_root: Path) -> str:
    safe_directory = repo_root.as_posix()
    try:
        head = subprocess.run(
            ["git", "-c", f"safe.directory={safe_directory}", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "-c", f"safe.directory={safe_directory}", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        raise V4RunnerError("evaluation_failed") from None
    if head.returncode != 0 or dirty.returncode != 0 or dirty.stdout.strip():
        raise V4RunnerError("evaluation_failed") from None
    candidate = head.stdout.strip()
    if not _SHA1.fullmatch(candidate):
        raise V4RunnerError("evaluation_failed") from None
    return candidate


def _validate_response_path(response_path: Path, home: Path) -> None:
    ipc_root = (home / "ipc").resolve(strict=True)
    if (
        not response_path.is_absolute()
        or response_path.is_symlink()
        or response_path.name.startswith("result-") is False
        or response_path.suffix != ".json"
        or response_path.parent.resolve(strict=True) != ipc_root
    ):
        raise ValueError("worker response path is unsafe")
    if response_path.exists():
        raise ValueError("worker response already exists")


def _write_response(path: Path, report: V4SafeAggregate) -> None:
    payload = report.to_json().encode("utf-8") + b"\n"
    if len(payload) > 16_385:
        raise ValueError("aggregate exceeds the IPC bound")
    descriptor, temporary = tempfile.mkstemp(prefix=".v4-ipc-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
