from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pytest

from aaos.moodle.bootstrap import WEBVIEW2_INTERACTION_TIMEOUT_SECONDS, MoodleBootstrapConfig
from aaos.moodle.models import MoodleSettings, resolve_auth_paths
from aaos.moodle.receiver import (
    ReceiverError,
    _read_user_environment,
    _runtime_configuration,
    begin_bootstrap_attempt,
    main,
)

NAMES = {
    "AAOS_MOODLE_SOURCE_SYSTEM": "moodle:just:summer-2026:bootstrap",
    "AAOS_MOODLE_BASE_ORIGIN": "https://elearn.just.edu.jo/current",
    "AAOS_MOODLE_AUTH_ALIAS": "just-summer-2026",
    "AAOS_DATA_ROOT": "C:\\Users\\aljaf\\aaos\\data",
}


def _set_process(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _callback(passport: str) -> str:
    config = MoodleBootstrapConfig(
        source_system=NAMES["AAOS_MOODLE_SOURCE_SYSTEM"],
        canonical_wwwroot=NAMES["AAOS_MOODLE_BASE_ORIGIN"],
        callback_kind="registered_uri",
        callback_scheme="aaos",
        timeout_seconds=300,
    )
    siteid = hashlib.md5((config.canonical_wwwroot + passport).encode()).hexdigest()
    envelope = f"{siteid}:::REST-TOKEN"
    encoded = __import__("base64").b64encode(envelope.encode()).decode()
    return f"aaos://token={encoded}"


def test_process_values_are_used_when_user_values_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_process(monkeypatch, NAMES)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: {})

    config, _ = _runtime_configuration()

    assert config.source_system == NAMES["AAOS_MOODLE_SOURCE_SYSTEM"]
    assert config.canonical_wwwroot == NAMES["AAOS_MOODLE_BASE_ORIGIN"]


def test_runtime_configuration_uses_the_human_interaction_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_process(monkeypatch, NAMES)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: {})

    config, _ = _runtime_configuration()

    assert config.timeout_seconds == WEBVIEW2_INTERACTION_TIMEOUT_SECONDS


def test_user_values_are_used_when_process_values_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: NAMES.copy())

    config, _ = _runtime_configuration()

    assert config.source_system == NAMES["AAOS_MOODLE_SOURCE_SYSTEM"]


def test_identical_process_and_user_values_are_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_process(monkeypatch, NAMES)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: NAMES.copy())

    _runtime_configuration()


def test_process_user_disagreement_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_process(monkeypatch, NAMES)
    user = NAMES.copy()
    user["AAOS_MOODLE_AUTH_ALIAS"] = "different"
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: user)

    with pytest.raises(ReceiverError) as error:
        _runtime_configuration()

    assert str(error.value) == "Moodle callback configuration conflict"


def test_missing_from_both_sources_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: {})

    assert main(["aaos://token=invalid"]) == 2


def test_malformed_user_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    user: dict[str, object] = dict(NAMES)
    user["AAOS_DATA_ROOT"] = 42
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: user)

    assert main(["aaos://token=invalid"]) == 2


@pytest.mark.parametrize(
    "name,bad_value",
    [
        ("AAOS_MOODLE_SOURCE_SYSTEM", ""),
        ("AAOS_MOODLE_AUTH_ALIAS", "bad\nvalue"),
        ("AAOS_DATA_ROOT", "relative\\data"),
    ],
)
def test_malformed_user_text_and_path_values_fail_closed(
    monkeypatch: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    for variable in NAMES:
        monkeypatch.delenv(variable, raising=False)
    user: dict[str, object] = dict(NAMES)
    user[name] = bad_value
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: user)

    assert main(["aaos://token=invalid"]) == 2


def test_user_only_configuration_reaches_safe_callback_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = MoodleSettings(
        source_system=NAMES["AAOS_MOODLE_SOURCE_SYSTEM"],
        base_origin="https://elearn.just.edu.jo",
        mode="api",
        auth_alias=NAMES["AAOS_MOODLE_AUTH_ALIAS"],
    )
    paths = resolve_auth_paths(settings, data_root=tmp_path)
    config = MoodleBootstrapConfig(
        source_system=NAMES["AAOS_MOODLE_SOURCE_SYSTEM"],
        canonical_wwwroot=NAMES["AAOS_MOODLE_BASE_ORIGIN"],
        callback_kind="registered_uri",
        callback_scheme="aaos",
        timeout_seconds=300,
    )
    attempt = begin_bootstrap_attempt(config, paths, now=int(time.time()))
    user = NAMES.copy()
    user["AAOS_DATA_ROOT"] = str(tmp_path)
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("aaos.moodle.receiver._read_user_environment", lambda: user)

    assert main([_callback(attempt.passport)]) == 0
    assert paths.api_token_path.read_text(encoding="utf-8") == "REST-TOKEN\n"


def test_registry_reader_queries_only_the_four_exact_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queried: list[str] = []

    class Key:
        def __enter__(self) -> "Key":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class FakeWinreg:
        HKEY_CURRENT_USER = object()
        KEY_READ = 1

        @staticmethod
        def OpenKey(*args: object) -> Key:
            assert args[0] is FakeWinreg.HKEY_CURRENT_USER
            assert args[1] == "Environment"
            assert args[2] == 0
            assert args[3] == FakeWinreg.KEY_READ
            return Key()

        @staticmethod
        def QueryValueEx(_key: Key, name: str) -> tuple[str, int]:
            queried.append(name)
            return NAMES[name], 1

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)

    assert _read_user_environment() == NAMES
    assert queried == list(NAMES)


def test_missing_registry_value_is_not_treated_as_valid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)

    class Key:
        def __enter__(self) -> "Key":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class FakeWinreg:
        HKEY_CURRENT_USER = object()
        KEY_READ = 1

        @staticmethod
        def OpenKey(*_: object) -> Key:
            return Key()

        @staticmethod
        def QueryValueEx(_key: Key, name: str) -> tuple[str, int]:
            if name == "AAOS_DATA_ROOT":
                raise FileNotFoundError(name)
            return NAMES[name], 1

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)

    assert main(["aaos://token=invalid"]) == 2


def test_non_windows_resolution_does_not_read_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_process(monkeypatch, NAMES)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "aaos.moodle.receiver._read_user_environment",
        lambda: (_ for _ in ()).throw(AssertionError("registry read")),
    )

    _runtime_configuration()
