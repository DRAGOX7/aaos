from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from aaos.academic.source import (
    CompleteSourceSnapshot,
    RawAnnouncement,
    RawAssignment,
    RawCourse,
    RawRemoteFile,
    SourceCollection,
    SourceCollectionFailure,
)
from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.collection import CourseDiscovery, EntityBundle, collect_complete
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import RuntimeMoodleEnvironment


def _environment(data_root: Path) -> RuntimeMoodleEnvironment:
    settings = MoodleSettings(
        source_system="moodle:fixture:live-sync",
        base_origin="https://moodle.example.edu",
        mode="browser",
        auth_alias="fixture",
    )
    return RuntimeMoodleEnvironment(
        bootstrap_config=MoodleBootstrapConfig(
            source_system=settings.source_system,
            canonical_wwwroot="https://moodle.example.edu/current",
            callback_kind="registered_uri",
            callback_scheme="aaos",
            timeout_seconds=60,
        ),
        settings=settings,
        auth_paths=AuthPaths(data_root / "credential", data_root / "auth"),
        data_root=data_root,
    )


def _complete_snapshot() -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system="moodle:fixture:live-sync",
        courses=(RawCourse(source_id="course-1", title="Course"),),
        assignments=(
            RawAssignment(source_id="assignment-1", course_source_id="course-1", title="Assignment"),
        ),
        announcements=(
            RawAnnouncement(
                source_id="announcement-1", course_source_id="course-1", title="Announcement"
            ),
        ),
        remote_files=(
            RawRemoteFile(source_id="file-1", course_source_id="course-1", title="File"),
        ),
    )


def _record_collection_call(
    calls: list[str], source: object, source_system: str
) -> CompleteSourceSnapshot:
    calls.append(f"collect_complete:{type(source).__name__}:{source_system}")
    return CompleteSourceSnapshot(source_system=source_system)


def test_configured_snapshot_derives_browser_mode_without_changing_source_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aaos.moodle import live_sync

    configured = _environment(tmp_path)
    configured = replace(
        configured,
        settings=replace(configured.settings, mode="api"),
    )
    observed: dict[str, object] = {}

    def collect(*, settings: MoodleSettings, **_kwargs: object) -> CompleteSourceSnapshot:
        observed["settings"] = settings
        return CompleteSourceSnapshot(source_system=settings.source_system)

    monkeypatch.setattr(live_sync, "_run_same_context_complete_collection", collect)

    result = live_sync.collect_configured_snapshot(configured)

    assert isinstance(result, CompleteSourceSnapshot)
    settings = observed["settings"]
    assert isinstance(settings, MoodleSettings)
    assert settings.mode == "browser"
    assert settings.source_system == configured.settings.source_system
    assert settings.base_origin == configured.settings.base_origin
    assert settings.auth_alias == configured.settings.auth_alias


def test_database_disappearing_after_collection_fails_closed_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from aaos.moodle import live_sync

    database_path = tmp_path / "database" / "aaos.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"already-migrated")
    environment = _environment(tmp_path)
    persistence_calls: list[object] = []

    def collect(_environment: RuntimeMoodleEnvironment) -> CompleteSourceSnapshot:
        database_path.unlink()
        return _complete_snapshot()

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", collect)
    monkeypatch.setattr(
        live_sync,
        "sync_moodle_snapshot",
        lambda *args, **_kwargs: persistence_calls.append(args[0]),
    )

    assert live_sync.run_configured_moodle_sync() == 1
    assert database_path.exists() is False
    assert persistence_calls == []
    assert capsys.readouterr().out == "MOODLE_SYNC_FAILED\nFAILURE_STAGE=PERSISTENCE\n"


def test_main_dispatches_only_exact_moodle_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    from aaos import cli

    calls: list[str] = []

    def dispatch() -> int:
        calls.append("sync")
        return 0

    monkeypatch.setattr(
        cli,
        "_run_configured_moodle_sync",
        dispatch,
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "sync"])

    assert cli.main() == 0
    assert calls == ["sync"]


def test_unsupported_moodle_command_remains_safe(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from aaos import cli

    called = False

    def forbidden() -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "_run_configured_moodle_sync", forbidden, raising=False)
    monkeypatch.setattr(sys, "argv", ["aaos", "moodle", "sync", "--retry"])

    assert cli.main() == 2
    assert called is False
    assert capsys.readouterr().err == "usage: aaos doctor\n"


def test_complete_collection_is_persisted_only_after_all_collection_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from aaos.moodle import live_sync

    database_path = tmp_path / "database" / "aaos.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"already-migrated")
    environment = _environment(tmp_path)
    events: list[str] = []
    engine = type("EngineProbe", (), {"dispose": lambda self: events.append("dispose")})()

    def collect(_environment: RuntimeMoodleEnvironment) -> CompleteSourceSnapshot:
        events.append("collect")
        return _complete_snapshot()

    def make_engine(path: Path) -> object:
        events.append(f"engine:{path}")
        return engine

    def make_session_factory(_engine: object) -> object:
        events.append("session-factory")
        return lambda: object()

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", collect)
    monkeypatch.setattr(live_sync, "_create_existing_database_engine", make_engine)
    monkeypatch.setattr(live_sync, "create_session_factory", make_session_factory)

    def persist(collection: object, *, session_factory: object) -> None:
        assert isinstance(collection, CompleteSourceSnapshot)
        assert callable(session_factory)
        events.append("persist")

    monkeypatch.setattr(live_sync, "sync_moodle_snapshot", persist)

    assert live_sync.run_configured_moodle_sync() == 0
    assert events == ["collect", f"engine:{database_path.resolve()}", "session-factory", "persist", "dispose"]
    assert capsys.readouterr().out == (
        "MOODLE_SYNC_PASS\n"
        "COURSE_COUNT=1\n"
        "ASSIGNMENT_COUNT=1\n"
        "ANNOUNCEMENT_COUNT=1\n"
        "REMOTE_FILE_COUNT=1\n"
    )


def test_incomplete_collection_blocks_engine_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from aaos.moodle import live_sync

    database_path = tmp_path / "database" / "aaos.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"already-migrated")
    events: list[str] = []
    environment = _environment(tmp_path)

    def collect(_environment: RuntimeMoodleEnvironment) -> SourceCollectionFailure:
        events.append("collect")
        return SourceCollectionFailure("moodle:fixture:live-sync", "browser_entity_incomplete")

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", collect)

    def forbidden_engine(_path: Path) -> object:
        events.append("engine")
        raise AssertionError("engine must not be created")

    def forbidden_persist(*_args: object, **_kwargs: object) -> None:
        events.append("persist")
        raise AssertionError("persistence must not run")

    monkeypatch.setattr(live_sync, "_create_existing_database_engine", forbidden_engine)
    monkeypatch.setattr(live_sync, "sync_moodle_snapshot", forbidden_persist)

    assert live_sync.run_configured_moodle_sync() == 1
    assert events == ["collect"]
    assert capsys.readouterr().out == "MOODLE_SYNC_FAILED\nFAILURE_STAGE=COLLECTION\n"


def test_missing_database_fails_before_collection_or_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from aaos.moodle import live_sync

    events: list[str] = []
    environment = _environment(tmp_path)

    def forbidden_collect(_environment: RuntimeMoodleEnvironment) -> SourceCollection:
        events.append("collect")
        raise AssertionError("collection must not run")

    def forbidden_engine(_path: Path) -> object:
        events.append("engine")
        raise AssertionError("engine must not be created")

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", forbidden_collect)
    monkeypatch.setattr(live_sync, "_create_existing_database_engine", forbidden_engine)

    assert live_sync.run_configured_moodle_sync() == 1
    assert events == []
    assert capsys.readouterr().out == "MOODLE_SYNC_FAILED\nFAILURE_STAGE=DATABASE\n"


def test_collection_failure_is_not_retried_and_exception_text_is_not_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    from aaos.moodle import live_sync

    database_path = tmp_path / "database" / "aaos.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"already-migrated")
    environment = _environment(tmp_path)
    calls = 0

    def collect(_environment: RuntimeMoodleEnvironment) -> SourceCollectionFailure:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret-token-and-private-html")

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", collect)

    assert live_sync.run_configured_moodle_sync() == 1
    assert calls == 1
    output = capsys.readouterr()
    assert "secret-token-and-private-html" not in output.out
    assert output.out == "MOODLE_SYNC_FAILED\nFAILURE_STAGE=COLLECTION\n"


def test_same_context_collection_uses_collect_complete_and_cleans_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aaos.academic.source import CompleteSourceSnapshot
    from aaos.moodle import browser

    context = _FakeContext()
    calls: list[str] = []

    monkeypatch.setattr(
        browser,
        "collect_complete",
        lambda source, *, source_system: _record_collection_call(
            calls, source, source_system
        ),
    )

    result = browser._run_same_context_complete_collection(
        settings=MoodleSettings(
            source_system="moodle:fixture:live-sync",
            base_origin="https://moodle.example.edu",
            mode="browser",
            auth_alias="fixture",
        ),
        paths=AuthPaths(tmp_path / "credential", tmp_path / "auth"),
        launch_persistent_context=lambda **_kwargs: context,
        canonical_wwwroot="https://moodle.example.edu/current",
    )

    assert isinstance(result, CompleteSourceSnapshot)
    assert calls == ["collect_complete:MoodleBrowserSource:moodle:fixture:live-sync"]
    assert context.closed is True


def test_same_context_collection_failure_still_cleans_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aaos.moodle import browser

    context = _FakeContext()
    monkeypatch.setattr(
        browser,
        "collect_complete",
        lambda _source, *, source_system: SourceCollectionFailure(source_system, "incomplete"),
    )

    result = browser._run_same_context_complete_collection(
        settings=MoodleSettings(
            source_system="moodle:fixture:live-sync",
            base_origin="https://moodle.example.edu",
            mode="browser",
            auth_alias="fixture",
        ),
        paths=AuthPaths(tmp_path / "credential", tmp_path / "auth"),
        launch_persistent_context=lambda **_kwargs: context,
    )

    assert isinstance(result, SourceCollectionFailure)
    assert context.closed is True


def test_collection_reads_every_course_bundle_before_sync(tmp_path: Path) -> None:
    events: list[str] = []

    class Source:
        def discover_courses(self) -> CourseDiscovery:
            events.append("discover")
            return CourseDiscovery(
                (
                    RawCourse(source_id="course-1", title="Course 1"),
                    RawCourse(source_id="course-2", title="Course 2"),
                ),
                is_complete=True,
            )

        def collect_course_entities(self, course: RawCourse) -> EntityBundle:
            events.append(f"entities:{course.source_id}")
            return EntityBundle(course_source_id=course.source_id, is_complete=True)

    collection = collect_complete(Source(), source_system="moodle:fixture:live-sync")
    events.append("sync")

    assert isinstance(collection, CompleteSourceSnapshot)
    assert events == ["discover", "entities:course-1", "entities:course-2", "sync"]


def test_no_alembic_upgrade_is_called_by_sync_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command

    from aaos.moodle import live_sync

    database_path = tmp_path / "database" / "aaos.db"
    database_path.parent.mkdir()
    database_path.write_bytes(b"already-migrated")
    environment = _environment(tmp_path)
    upgrades: list[object] = []
    engine = type("EngineProbe", (), {"dispose": lambda self: None})()

    monkeypatch.setattr(live_sync, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(live_sync, "collect_configured_snapshot", lambda _environment: _complete_snapshot())
    monkeypatch.setattr(live_sync, "_create_existing_database_engine", lambda _path: engine)
    monkeypatch.setattr(live_sync, "create_session_factory", lambda _engine: lambda: object())
    monkeypatch.setattr(live_sync, "sync_moodle_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(command, "upgrade", lambda *args, **kwargs: upgrades.append((args, kwargs)))

    assert live_sync.run_configured_moodle_sync() == 0
    assert upgrades == []


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = []
        self.closed = False

    def new_page(self) -> "_FakePage":
        kind = "auth" if not self.pages else "sentinel"
        page = _FakePage(self, kind)
        self.pages.append(page)
        return page

    def route(self, _pattern: str, _handler: object) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.pages.clear()


class _FakePage:
    def __init__(self, context: _FakeContext, kind: str) -> None:
        self._context = context
        self._kind = kind
        self.url = "about:blank" if kind == "sentinel" else ""

    def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout == 600_000
        self.url = "https://moodle.example.edu/current/my/courses.php"

    def content(self) -> str:
        return "<body class='page-mycourses'></body>"

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def close(self) -> None:
        self._context.pages.remove(self)
