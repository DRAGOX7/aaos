from __future__ import annotations

from pathlib import Path

import pytest

import aaos.agent.evaluation.v4_worker as v4_worker
from aaos.agent.evaluation.v4_runner import V4RunnerError

SECRET = "SECRET_V4_CASE_DO_NOT_LEAK"


@pytest.mark.parametrize(
    ("runner_error", "expected_exit_code"),
    [("manifest_failed", 10), ("report_write_failed", 11)],
)
def test_manifest_worker_maps_runner_failures_to_closed_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runner_error: str,
    expected_exit_code: int,
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def verify_manifest(self) -> None:
            raise V4RunnerError(runner_error)

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == expected_exit_code
    assert captured.out == ""
    assert captured.err == ""
    assert not response_path.exists()
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()


def test_manifest_worker_does_not_print_or_propagate_synthetic_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    local_appdata = tmp_path / "local-appdata"
    repository = tmp_path / "repo"
    response_dir = local_appdata / "AAOS" / "evaluation" / "v4" / "ipc"
    response_dir.mkdir(parents=True)
    repository.mkdir()
    response_path = response_dir / "result-synthetic.json"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(v4_worker, "_candidate_sha", lambda _: "a" * 40)

    class _SyntheticRunner:
        def __init__(self, **kwargs: object) -> None:
            pass

        def verify_manifest(self) -> None:
            raise RuntimeError(SECRET)

    monkeypatch.setattr(v4_worker, "V4SealedRunner", _SyntheticRunner)

    result = v4_worker.main(["manifest", str(response_path), str(repository)])
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == ""
    assert SECRET not in captured.out + captured.err
    assert not response_path.exists()
    assert not (
        local_appdata / "AAOS" / "evaluation" / "v4" / "reports" / "manifest-latest.json"
    ).exists()
