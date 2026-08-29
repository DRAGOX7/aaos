from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from aaos.documents.cli import (
    Phase3ServiceRuntime,
    RuntimeMoodleEnvironment,
    build_phase3_service,
    load_runtime_moodle_environment,
    render_batch_result,
)
from aaos.documents.config import DocumentRuntimePaths
from aaos.documents.contracts import (
    BatchIngestionResult,
    DocumentIngestionFailure,
    DocumentIngestionResult,
    DocumentResultCode,
    IngestionErrorCode,
    IngestionMode,
)
from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.models import AuthPaths, MoodleSettings

SOURCE = "moodle:just:phase3"
ORIGIN = "https://elearn.just.edu.jo"
WWWROOT = f"{ORIGIN}/current"
ALIAS = "just-phase3"


def batch(*, failed: int = 0) -> BatchIngestionResult:
    code = DocumentResultCode.FAILED if failed else DocumentResultCode.INGESTED
    document = DocumentIngestionResult(
        remote_file_id=17,
        code=code,
        version_number=None if failed else 1,
        size_bytes=None if failed else 128,
        page_count=None if failed else 2,
        deduplicated=False,
        error_code=IngestionErrorCode.INVALID_PDF if failed else None,
    )
    return BatchIngestionResult(
        remote_files_selected=1,
        ingested=0 if failed else 1,
        unchanged=0,
        failed=failed,
        skipped=0,
        new_versions=0 if failed else 1,
        deduplicated_blobs=0,
        documents=(document,),
    )


@dataclass
class FakeService:
    calls: list[tuple[IngestionMode, str, int | None]]
    result: BatchIngestionResult = batch()

    def run(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None = None,
    ) -> BatchIngestionResult:
        self.calls.append((mode, source_system, remote_file_id))
        return self.result


@dataclass
class FakeRuntime:
    service: FakeService
    closed: bool = False

    def __enter__(self) -> "FakeRuntime":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.closed = True


def environment(tmp_path: Path) -> RuntimeMoodleEnvironment:
    settings = MoodleSettings(SOURCE, ORIGIN, "browser", ALIAS)
    return RuntimeMoodleEnvironment(
        bootstrap_config=MoodleBootstrapConfig(
            source_system=SOURCE,
            canonical_wwwroot=WWWROOT,
            callback_kind="registered_uri",
            callback_scheme="aaos",
            timeout_seconds=600,
        ),
        settings=settings,
        auth_paths=AuthPaths(
            api_token_path=tmp_path / "credentials" / "token",
            browser_state_dir=tmp_path / "auth",
        ),
        data_root=tmp_path,
    )


def run_with_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    *,
    result: BatchIngestionResult | None = None,
) -> tuple[int, FakeService]:
    env = environment(tmp_path)
    service = FakeService([], result or batch())
    runtime = FakeRuntime(service)
    import aaos.documents.cli as module

    monkeypatch.setattr(module, "load_runtime_moodle_environment", lambda: env)
    monkeypatch.setattr(module, "build_phase3_service", lambda **_: runtime)
    exit_code = module.run_documents_command(argv)
    return exit_code, service


def _migrated_phase3_setup(
    tmp_path: Path,
) -> tuple[RuntimeMoodleEnvironment, DocumentRuntimePaths]:
    env = environment(tmp_path)
    database_path = tmp_path / "database" / "phase3.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(config, "0003_phase3_document_ingestion")
    return env, DocumentRuntimePaths(
        database_path=database_path,
        content_store_root=tmp_path / "documents",
        ingestion_lock_path=tmp_path / "temporary" / "lock",
    )


def _spy_composition_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Engine], list[int]]:
    import aaos.documents.cli as module
    import aaos.persistence.database as database_module

    engines: list[Engine] = []
    dispose_counts: list[int] = []

    def create_spied_engine(path: Path) -> Engine:
        engine = database_module.create_database_engine(path)
        index = len(engines)
        original_dispose = engine.dispose

        def dispose(close: bool = True) -> None:
            dispose_counts[index] += 1
            original_dispose(close)

        engine.dispose = dispose  # type: ignore[method-assign]
        engines.append(engine)
        dispose_counts.append(0)
        return engine

    monkeypatch.setattr(module, "create_database_engine", create_spied_engine)
    return engines, dispose_counts


@pytest.mark.parametrize("command", ["ingest-new", "refresh"])
def test_document_modes_map_to_exact_service_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    exit_code, service = run_with_fake(monkeypatch, tmp_path, [command])
    assert exit_code == 0
    assert service.calls == [
        (
            IngestionMode.INGEST_NEW if command == "ingest-new" else IngestionMode.REFRESH,
            SOURCE,
            None,
        )
    ]


def test_positive_internal_id_is_forwarded_and_no_source_selector_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exit_code, service = run_with_fake(monkeypatch, tmp_path, ["refresh", "--remote-file-id", "17"])
    assert exit_code == 0
    assert service.calls == [(IngestionMode.REFRESH, SOURCE, 17)]


@pytest.mark.parametrize(
    "argv",
    [
        ["ingest-new", "--remote-file-id", "0"],
        ["refresh", "--remote-file-id", "-1"],
        ["refresh", "--remote-file-id", "https://example.test"],
        ["refresh", "--remote-file-id", "resource-cm:3"],
        ["refresh", "--remote-file-id", "17", "--remote-file-id", "18"],
        ["refresh", "--unknown"],
        ["refresh", "extra"],
        ["unknown"],
        [],
    ],
)
def test_invalid_document_grammar_returns_two_without_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> None:
    exit_code, service = run_with_fake(monkeypatch, tmp_path, argv)
    assert exit_code == 2
    assert service.calls == []


def test_failed_document_returns_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exit_code, service = run_with_fake(
        monkeypatch, tmp_path, ["ingest-new"], result=batch(failed=1)
    )
    assert exit_code == 1
    assert service.calls


def test_render_result_contains_only_fixed_operational_fields() -> None:
    rendered = "\n".join(render_batch_result(batch()))
    assert "REMOTE_FILES_SELECTED=1" in rendered
    for field in (
        "INGESTED",
        "UNCHANGED",
        "FAILED",
        "SKIPPED",
        "NEW_VERSIONS",
        "DEDUPLICATED_BLOBS",
    ):
        assert f"{field}=" in rendered
    for sentinel in ("token-secret", "https://private", "filename.pdf", "page text"):
        assert sentinel not in rendered


def test_malformed_result_fails_without_rendering_untrusted_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import aaos.documents.cli as module

    env = environment(tmp_path)
    sentinel = "token-secret https://private control-value"
    forged = SimpleNamespace(
        remote_files_selected=sentinel,
        ingested=0,
        unchanged=0,
        failed=0,
        skipped=0,
        new_versions=0,
        deduplicated_blobs=0,
        documents=(),
    )

    class MaliciousService:
        def run(self, **_: object) -> BatchIngestionResult:
            return cast(BatchIngestionResult, forged)

    monkeypatch.setattr(module, "load_runtime_moodle_environment", lambda: env)
    monkeypatch.setattr(module, "build_phase3_service", lambda **_: MaliciousService())

    assert module.run_documents_command(["refresh"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "DOCUMENT_INGESTION_FAILED\n"
    assert sentinel not in captured.out + captured.err


def test_import_and_help_do_not_construct_runtime_or_create_paths() -> None:
    module = importlib.import_module("aaos.documents.cli")
    assert module is not None
    assert not Path("data/database/aaos.db").exists()
    assert not Path("data/documents").exists()


def test_document_cli_import_does_not_load_phase2_collection_or_sync_modules() -> None:
    script = (
        "import sys; import aaos.documents.cli; "
        "forbidden = ('aaos.academic.normalization', 'aaos.academic.sync', "
        "'aaos.moodle.browser_source', 'aaos.moodle.browser'); "
        "assert not any(name in sys.modules for name in forbidden)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_runtime_environment_preserves_canonical_wwwroot_and_browser_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "AAOS_MOODLE_SOURCE_SYSTEM": SOURCE,
        "AAOS_MOODLE_BASE_ORIGIN": WWWROOT,
        "AAOS_MOODLE_AUTH_ALIAS": ALIAS,
        "AAOS_DATA_ROOT": str(tmp_path.resolve()),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "platform", "linux")
    env = load_runtime_moodle_environment()
    assert env.bootstrap_config.canonical_wwwroot == WWWROOT
    assert env.settings.mode == "browser"
    assert env.settings.base_origin == ORIGIN
    assert env.data_root == tmp_path.resolve()


def test_build_requires_existing_migrated_database(
    tmp_path: Path,
) -> None:
    env = environment(tmp_path)
    paths = DocumentRuntimePaths(
        database_path=tmp_path / "missing.db",
        content_store_root=tmp_path / "documents",
        ingestion_lock_path=tmp_path / "temporary" / "lock",
    )
    with pytest.raises(Exception) as caught:
        build_phase3_service(
            bootstrap_config=env.bootstrap_config,
            settings=env.settings,
            auth_paths=env.auth_paths,
            paths=paths,
        )
    assert "missing.db" not in str(caught.value)


def test_build_binds_canonical_wwwroot_and_source_system_explicitly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aaos.documents.cli as module

    env = environment(tmp_path)
    paths = DocumentRuntimePaths(
        database_path=tmp_path / "existing.db",
        content_store_root=tmp_path / "documents",
        ingestion_lock_path=tmp_path / "temporary" / "lock",
    )
    paths.database_path.touch()
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(module, "_database_is_migrated", lambda _path: None)
    monkeypatch.setattr(module, "create_database_engine", lambda _path: object())
    monkeypatch.setattr(module, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(module, "DocumentRepository", lambda _factory: object())
    monkeypatch.setattr(module, "ContentBlobStore", lambda _root: object())
    monkeypatch.setattr(module, "IngestionLock", lambda _path: object())
    monkeypatch.setattr(module, "PdfProcessor", lambda: object())
    monkeypatch.setattr(module, "Phase3IngestionService", FakeService)

    def fake_fetcher_factory(**kwargs: object) -> object:
        captured["fetcher_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(module, "open_phase3_fetcher_session", fake_fetcher_factory)
    runtime = build_phase3_service(
        bootstrap_config=env.bootstrap_config,
        settings=env.settings,
        auth_paths=env.auth_paths,
        paths=paths,
    )
    assert isinstance(runtime, Phase3ServiceRuntime)
    service = runtime.service
    assert isinstance(service, FakeService)
    factory = captured["fetcher_factory"]
    assert callable(factory)
    factory()
    fetcher_kwargs = captured["fetcher_kwargs"]
    assert isinstance(fetcher_kwargs, dict)
    assert fetcher_kwargs["canonical_wwwroot"] == WWWROOT
    assert fetcher_kwargs["expected_source_system"] == SOURCE
    assert fetcher_kwargs["settings"] is env.settings
    assert fetcher_kwargs["auth_paths"] is env.auth_paths


def test_built_phase3_service_exposes_idempotent_engine_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A composed disposable database must have a deterministic engine release path."""
    env, paths = _migrated_phase3_setup(tmp_path)
    engines, dispose_counts = _spy_composition_engines(monkeypatch)
    runtime = build_phase3_service(
        bootstrap_config=env.bootstrap_config,
        settings=env.settings,
        auth_paths=env.auth_paths,
        paths=paths,
    )

    runtime.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE)
    assert len(engines) == 2
    release = getattr(runtime, "close", None)
    assert callable(release)
    release()
    release()
    assert dispose_counts[1] == 1


def test_built_phase3_service_context_manager_releases_engine_on_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env, paths = _migrated_phase3_setup(tmp_path)
    engines, dispose_counts = _spy_composition_engines(monkeypatch)

    with build_phase3_service(
        bootstrap_config=env.bootstrap_config,
        settings=env.settings,
        auth_paths=env.auth_paths,
        paths=paths,
    ) as runtime:
        runtime.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE)

    assert len(engines) == 2
    assert dispose_counts[1] == 1


def test_built_phase3_runtime_allows_disposable_database_cleanup(
    tmp_path: Path,
) -> None:
    env, paths = _migrated_phase3_setup(tmp_path)

    with build_phase3_service(
        bootstrap_config=env.bootstrap_config,
        settings=env.settings,
        auth_paths=env.auth_paths,
        paths=paths,
    ) as runtime:
        runtime.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE)

    paths.database_path.unlink()
    paths.database_path.parent.rmdir()
    assert not paths.database_path.exists()


def test_run_documents_command_closes_runtime_when_service_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import aaos.documents.cli as module

    env, paths = _migrated_phase3_setup(tmp_path)
    engines, dispose_counts = _spy_composition_engines(monkeypatch)

    class FailingService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, **_kwargs: object) -> BatchIngestionResult:
            raise DocumentIngestionFailure(IngestionErrorCode.NETWORK_FAILURE)

    monkeypatch.setattr(module, "Phase3IngestionService", FailingService)
    monkeypatch.setattr(module, "load_runtime_moodle_environment", lambda: env)
    monkeypatch.setattr(module, "resolve_document_runtime_paths", lambda _root: paths)

    assert module.run_documents_command(["refresh"]) == 1
    assert len(engines) == 2
    assert dispose_counts[1] == 1


def test_run_documents_command_closes_runtime_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = environment(tmp_path)
    runtime = FakeRuntime(FakeService([], batch()))
    import aaos.documents.cli as module

    monkeypatch.setattr(module, "load_runtime_moodle_environment", lambda: env)
    monkeypatch.setattr(module, "build_phase3_service", lambda **_: runtime)

    assert module.run_documents_command(["refresh"]) == 0
    assert runtime.closed


def test_main_routes_documents_without_eagerly_running_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.cli as cli
    import aaos.documents.cli as document_cli

    calls: list[tuple[str, ...]] = []

    def run_documents(arguments: Sequence[str]) -> int:
        calls.append(tuple(arguments))
        return 7

    monkeypatch.setattr(document_cli, "run_documents_command", run_documents)
    monkeypatch.setattr(sys, "argv", ["aaos", "documents", "refresh"])
    assert cli.main() == 7
    assert calls == [("refresh",)]
