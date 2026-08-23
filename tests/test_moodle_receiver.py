from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Callable, NoReturn, cast

import pytest

from aaos.moodle import receiver as receiver_module
from aaos.moodle.auth import CredentialWriteError
from aaos.moodle.bootstrap import (
    ATTEMPT_EXPIRY_SAFETY_SECONDS,
    CALLBACK_DELIVERY_GRACE_SECONDS,
    NATIVE_CLEANUP_ALLOWANCE_SECONDS,
    WEBVIEW2_INTERACTION_TIMEOUT_SECONDS,
    WEBVIEW2_SETUP_ALLOWANCE_SECONDS,
    MoodleBootstrapConfig,
    helper_process_timeout_seconds,
)
from aaos.moodle.models import AuthPaths, MoodleSettings, resolve_auth_paths
from aaos.moodle.receiver import (
    PENDING_ATTEMPT_MAX_BYTES,
    PENDING_ATTEMPT_TTL_SECONDS,
    PendingBootstrapAttempt,
    ReceiverError,
    begin_bootstrap_attempt,
    cancel_bootstrap_attempt,
    main,
    read_receiver_status,
    receive_callback,
    wait_for_bootstrap_completion,
)

CONFIG = MoodleBootstrapConfig(
    source_system="moodle:just:current",
    canonical_wwwroot="https://elearn.just.edu.jo/current",
    callback_kind="registered_uri",
    callback_scheme="aaos",
    timeout_seconds=300,
)


def test_human_auth_timeout_policy_is_aligned_and_bounded() -> None:
    assert WEBVIEW2_INTERACTION_TIMEOUT_SECONDS == 600
    assert WEBVIEW2_SETUP_ALLOWANCE_SECONDS == 60
    assert CALLBACK_DELIVERY_GRACE_SECONDS == 60
    assert NATIVE_CLEANUP_ALLOWANCE_SECONDS == 5
    assert ATTEMPT_EXPIRY_SAFETY_SECONDS == 10
    assert PENDING_ATTEMPT_TTL_SECONDS > (
        WEBVIEW2_INTERACTION_TIMEOUT_SECONDS
        + WEBVIEW2_SETUP_ALLOWANCE_SECONDS
        + CALLBACK_DELIVERY_GRACE_SECONDS
    )
    assert PENDING_ATTEMPT_TTL_SECONDS == (
        WEBVIEW2_INTERACTION_TIMEOUT_SECONDS
        + WEBVIEW2_SETUP_ALLOWANCE_SECONDS
        + CALLBACK_DELIVERY_GRACE_SECONDS
        + ATTEMPT_EXPIRY_SAFETY_SECONDS
    )
    assert PENDING_ATTEMPT_TTL_SECONDS == 730
    assert helper_process_timeout_seconds(WEBVIEW2_INTERACTION_TIMEOUT_SECONDS) > (
        WEBVIEW2_INTERACTION_TIMEOUT_SECONDS
        + WEBVIEW2_SETUP_ALLOWANCE_SECONDS
        + NATIVE_CLEANUP_ALLOWANCE_SECONDS
    )
    assert helper_process_timeout_seconds(WEBVIEW2_INTERACTION_TIMEOUT_SECONDS) == 670


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(
        api_token_path=tmp_path / "credentials" / "api_token",
        browser_state_dir=tmp_path / "auth",
    )


def _callback(passport: str, *, token: str = "REST-TOKEN", private: str | None = None) -> str:
    siteid = hashlib.md5((CONFIG.canonical_wwwroot + passport).encode()).hexdigest()
    envelope = f"{siteid}:::{token}"
    if private is not None:
        envelope += f":::{private}"
    encoded = base64.b64encode(envelope.encode()).decode()
    return f"aaos://token={encoded}"


def test_begin_writes_bounded_pending_attempt_without_token(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    pending = paths.browser_state_dir / "pending_bootstrap.json"
    document = json.loads(pending.read_text(encoding="utf-8"))
    assert attempt.expires_at == 1_000 + PENDING_ATTEMPT_TTL_SECONDS
    assert document["source_system"] == CONFIG.source_system
    assert document["canonical_wwwroot"] == CONFIG.canonical_wwwroot
    assert document["passport"] == attempt.passport
    assert "token" not in document
    assert "private_token" not in document
    assert "cookie" not in document


def test_only_one_active_attempt_per_source(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    with pytest.raises(ReceiverError) as error:
        begin_bootstrap_attempt(CONFIG, paths, now=1_001)

    assert str(error.value) == "Moodle callback receiver failed"


def test_active_attempt_lock_rejects_concurrent_creator(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.browser_state_dir.mkdir(parents=True)
    (paths.browser_state_dir / ".pending_bootstrap.lock").write_text("", encoding="utf-8")

    with pytest.raises(ReceiverError):
        begin_bootstrap_attempt(CONFIG, paths, now=1_000)


def test_expired_attempt_is_replaced_and_cancel_is_exact(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    replacement = begin_bootstrap_attempt(
        CONFIG, paths, now=1_000 + PENDING_ATTEMPT_TTL_SECONDS + 1
    )

    assert replacement.expires_at > 1_000 + PENDING_ATTEMPT_TTL_SECONDS
    cancel_bootstrap_attempt(paths, replacement.attempt_id)
    assert not (paths.browser_state_dir / "pending_bootstrap.json").exists()


def test_concurrent_creators_persist_only_the_successful_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    barrier = threading.Barrier(2)
    original_acquire = receiver_module._acquire_lock

    def synchronized_acquire(lock_paths: AuthPaths) -> Path:
        barrier.wait(timeout=2)
        return original_acquire(lock_paths)

    monkeypatch.setattr(receiver_module, "_acquire_lock", synchronized_acquire)

    def create() -> PendingBootstrapAttempt | None:
        try:
            return begin_bootstrap_attempt(CONFIG, paths, now=1_000)
        except ReceiverError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results: list[PendingBootstrapAttempt | None] = list(
            executor.map(lambda _: create(), range(2))
        )

    successes = [result for result in results if result is not None]
    assert len(successes) == 1
    document = json.loads(
        (paths.browser_state_dir / "pending_bootstrap.json").read_text(encoding="utf-8")
    )
    assert document["passport"] == successes[0].passport


def test_delayed_cancel_cannot_delete_a_newer_attempt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    first = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    cancel_bootstrap_attempt(paths, first.attempt_id)
    second = begin_bootstrap_attempt(CONFIG, paths, now=1_001)

    cancel_bootstrap_attempt(paths, first.attempt_id)

    pending = json.loads(
        (paths.browser_state_dir / "pending_bootstrap.json").read_text(encoding="utf-8")
    )
    assert pending["attempt_id"] == second.attempt_id


def test_valid_callback_persists_only_web_service_token_and_consumes_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    receive_callback(CONFIG, paths, _callback(attempt.passport, private="PRIVATE"), now=1_001)

    assert paths.api_token_path.read_text(encoding="utf-8") == "REST-TOKEN\n"
    assert not (paths.browser_state_dir / "pending_bootstrap.json").exists()


def test_valid_callback_publishes_a_non_secret_completion_for_the_same_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)

    completion = paths.browser_state_dir / "bootstrap_completion.json"
    assert completion.is_file()
    document = json.loads(completion.read_text(encoding="utf-8"))
    assert document == {
        "source_system": CONFIG.source_system,
        "canonical_wwwroot": CONFIG.canonical_wwwroot,
        "attempt_id": attempt.attempt_id,
        "completed_at": 1_001,
    }
    serialized = completion.read_text(encoding="utf-8")
    assert "REST-TOKEN" not in serialized
    assert attempt.passport not in serialized


def test_valid_callback_persists_only_a_completed_receiver_status_for_its_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)

    status = read_receiver_status(paths, attempt)
    serialized = (paths.browser_state_dir / "receiver_status.json").read_text(encoding="utf-8")
    document = json.loads(serialized)
    assert status == "COMPLETED"
    assert set(document) == {"status", "attempt_id", "recorded_at"}
    assert attempt.passport not in serialized
    assert "REST-TOKEN" not in serialized
    assert "aaos://" not in serialized


def test_rejected_callback_persists_only_a_rejected_receiver_status_for_its_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    with pytest.raises(ReceiverError):
        receive_callback(CONFIG, paths, _callback("a-different-passport"), now=1_001)

    assert read_receiver_status(paths, attempt) == "REJECTED"


def test_receiver_records_invocation_before_credential_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    def reject_credential_write(_: AuthPaths, __: str) -> None:
        assert read_receiver_status(paths, attempt) == "INVOKED"
        raise CredentialWriteError("credential unavailable")

    monkeypatch.setattr(receiver_module, "write_api_token", reject_credential_write)

    with pytest.raises(ReceiverError):
        receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)

    assert read_receiver_status(paths, attempt) == "INVOKED"


def test_receiver_status_stays_after_completion_and_pending_cleanup(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)

    wait_for_bootstrap_completion(
        paths,
        attempt,
        timeout_seconds=1,
        clock=lambda: 1_001.0,
        sleeper=lambda _: None,
    )
    cancel_bootstrap_attempt(paths, attempt.attempt_id)

    assert read_receiver_status(paths, attempt) == "COMPLETED"


def test_new_attempt_clears_the_previous_attempt_receiver_status(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    first = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    receive_callback(CONFIG, paths, _callback(first.passport), now=1_001)

    second = begin_bootstrap_attempt(CONFIG, paths, now=1_002)

    assert read_receiver_status(paths, second) == "NOT_INVOKED"


def test_previous_attempt_status_cannot_be_interpreted_as_the_current_attempt(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    first = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    receive_callback(CONFIG, paths, _callback(first.passport), now=1_001)
    status_path = paths.browser_state_dir / "receiver_status.json"
    first_status = status_path.read_bytes()

    second = begin_bootstrap_attempt(CONFIG, paths, now=1_002)
    status_path.write_bytes(first_status)

    assert read_receiver_status(paths, second) == "UNAVAILABLE"


def test_completion_is_bound_to_the_exact_attempt_and_consumed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)

    completion = wait_for_bootstrap_completion(
        paths,
        attempt,
        timeout_seconds=1,
        clock=lambda: 1_001.0,
        sleeper=lambda _: None,
    )

    assert completion.attempt_id == attempt.attempt_id
    assert not (paths.browser_state_dir / "bootstrap_completion.json").exists()


def test_callback_wait_stops_at_attempt_expiry_before_its_nominal_deadline(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    sleeper_calls: list[float] = []

    with pytest.raises(ReceiverError):
        wait_for_bootstrap_completion(
            paths,
            attempt,
            timeout_seconds=CALLBACK_DELIVERY_GRACE_SECONDS,
            clock=lambda: float(attempt.expires_at + 1),
            monotonic=lambda: 0.0,
            sleeper=sleeper_calls.append,
        )

    assert sleeper_calls == []


def test_mismatched_completion_fails_closed_without_reading_a_credential(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    paths.browser_state_dir.mkdir(parents=True, exist_ok=True)
    (paths.browser_state_dir / "bootstrap_completion.json").write_text(
        json.dumps(
            {
                "source_system": CONFIG.source_system,
                "canonical_wwwroot": CONFIG.canonical_wwwroot,
                "attempt_id": "different-attempt-id",
                "completed_at": 1_001,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReceiverError):
        wait_for_bootstrap_completion(
            paths,
            attempt,
            timeout_seconds=1,
            clock=lambda: 1_001.0,
            sleeper=lambda _: None,
        )


def test_expired_completion_fails_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    paths.browser_state_dir.mkdir(parents=True, exist_ok=True)
    (paths.browser_state_dir / "bootstrap_completion.json").write_text(
        json.dumps(
            {
                "source_system": CONFIG.source_system,
                "canonical_wwwroot": CONFIG.canonical_wwwroot,
                "attempt_id": attempt.attempt_id,
                "completed_at": attempt.expires_at + 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReceiverError):
        wait_for_bootstrap_completion(
            paths,
            attempt,
            timeout_seconds=1,
            clock=lambda: float(attempt.expires_at),
            sleeper=lambda _: None,
        )


def test_replayed_callback_is_rejected_without_overwriting_token(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    callback = _callback(attempt.passport, token="FIRST")
    receive_callback(CONFIG, paths, callback, now=1_001)

    with pytest.raises(ReceiverError) as error:
        receive_callback(CONFIG, paths, callback, now=1_002)

    assert str(error.value) == "Moodle callback receiver failed"
    assert paths.api_token_path.read_text(encoding="utf-8") == "FIRST\n"


def test_wrong_passport_consumes_attempt_without_writing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    with pytest.raises(ReceiverError):
        receive_callback(CONFIG, paths, _callback("a-different-passport"), now=1_001)

    assert not (paths.browser_state_dir / "pending_bootstrap.json").exists()
    assert not paths.api_token_path.exists()


@pytest.mark.parametrize(
    "callback_factory",
    [
        lambda passport: "moodlemobile://token=bad",
        lambda passport: "aaos://token=bad",
        lambda passport: "aaos://token=" + ("A" * 9000),
    ],
)
def test_invalid_callbacks_consume_attempt_without_overwriting_credential(
    tmp_path: Path, callback_factory: object
) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    paths.api_token_path.parent.mkdir(parents=True)
    paths.api_token_path.write_text("EXISTING\n", encoding="utf-8")
    callback = callback_factory(attempt.passport)  # type: ignore[operator]

    with pytest.raises(ReceiverError):
        receive_callback(CONFIG, paths, callback, now=1_001)

    assert not (paths.browser_state_dir / "pending_bootstrap.json").exists()
    assert paths.api_token_path.read_text(encoding="utf-8") == "EXISTING\n"


def test_wrong_source_configuration_does_not_consume_attempt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    wrong_config = replace(CONFIG, source_system="moodle:other")

    with pytest.raises(ReceiverError):
        receive_callback(wrong_config, paths, _callback(attempt.passport), now=1_001)

    assert (paths.browser_state_dir / "pending_bootstrap.json").exists()


def test_expired_callback_is_rejected_without_network_or_write(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)
    paths.api_token_path.parent.mkdir(parents=True)
    paths.api_token_path.write_text("EXISTING\n", encoding="utf-8")

    with pytest.raises(ReceiverError):
        receive_callback(
            CONFIG,
            paths,
            _callback(attempt.passport),
            now=1_000 + PENDING_ATTEMPT_TTL_SECONDS + 1,
        )

    assert not (paths.browser_state_dir / "pending_bootstrap.json").exists()
    assert paths.api_token_path.read_text(encoding="utf-8") == "EXISTING\n"


def test_expiry_boundary_is_still_valid(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    receive_callback(
        CONFIG,
        paths,
        _callback(attempt.passport),
        now=attempt.expires_at,
    )

    assert paths.api_token_path.exists()


def test_oversized_pending_state_is_rejected_with_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    paths.browser_state_dir.mkdir(parents=True)
    pending = paths.browser_state_dir / "pending_bootstrap.json"
    pending.write_bytes(b"{" + (b"A" * 4096))
    requested_sizes: list[int] = []
    original_open: Callable[..., BinaryIO] = cast(Any, Path.open)

    class Probe:
        def __init__(self, handle: BinaryIO) -> None:
            self._handle = handle

        def __enter__(self) -> "Probe":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self._handle.close()

        def read(self, size: int) -> bytes:
            requested_sizes.append(size)
            return self._handle.read(size)

    def open_probe(self: Path, *args: object, **kwargs: object) -> Probe:
        handle = original_open(self, *args, **kwargs)
        return Probe(handle)

    monkeypatch.setattr(Path, "open", open_probe)

    with pytest.raises(ReceiverError):
        receive_callback(CONFIG, paths, "aaos://token=unused", now=1_000)

    assert requested_sizes == [PENDING_ATTEMPT_MAX_BYTES + 1]


def test_receiver_does_not_open_network_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_socket(*_: object, **__: object) -> NoReturn:
        raise AssertionError("receiver attempted network access")

    monkeypatch.setattr(socket, "socket", fail_socket)
    paths = _paths(tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=1_000)

    receive_callback(CONFIG, paths, _callback(attempt.passport), now=1_001)
    assert paths.api_token_path.exists()


@pytest.mark.parametrize("argv", [[], ["aaos://token=one", "extra"]])
def test_main_rejects_missing_or_extra_arguments(argv: list[str]) -> None:
    assert main(argv) == 2


def test_main_uses_fixed_safe_exit_for_invalid_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AAOS_MOODLE_SOURCE_SYSTEM", raising=False)
    monkeypatch.delenv("AAOS_MOODLE_BASE_ORIGIN", raising=False)
    monkeypatch.delenv("AAOS_MOODLE_AUTH_ALIAS", raising=False)

    assert main(["aaos://token=one"]) == 2


def test_main_valid_activation_is_silent_and_uses_stable_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = MoodleSettings(
        source_system=CONFIG.source_system,
        base_origin="https://elearn.just.edu.jo",
        mode="api",
        auth_alias="just",
    )
    paths = resolve_auth_paths(settings, data_root=tmp_path)
    attempt = begin_bootstrap_attempt(CONFIG, paths, now=int(time.time()))
    monkeypatch.setenv("AAOS_MOODLE_SOURCE_SYSTEM", CONFIG.source_system)
    monkeypatch.setenv("AAOS_MOODLE_BASE_ORIGIN", CONFIG.canonical_wwwroot)
    monkeypatch.setenv("AAOS_MOODLE_AUTH_ALIAS", "just")
    monkeypatch.setenv("AAOS_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: {})

    assert main([_callback(attempt.passport)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
