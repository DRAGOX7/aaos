from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class FakeGateway:
    def __init__(self, calls: list[str], *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._calls = calls

    async def aclose(self) -> None:
        self._calls.append("gateway-close")


def _fake_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        service=object(),
        indexing=object(),
        close=lambda: None,
    )


def test_cli_configured_runtime_validates_database_and_injects_existing_only_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2

    import aaos.inference.lm_studio as lm_studio
    import aaos.rag.cli as cli
    import aaos.rag.runtime as runtime_module

    calls: list[str] = []
    validated_paths: list[Path] = []
    captured: dict[str, Any] = {}

    def validate(database_path: Path) -> None:
        calls.append("validate")
        validated_paths.append(database_path)

    def existing_only_engine_factory(database_path: Path) -> object:
        raise AssertionError(f"fake runtime must not actually open database {database_path}")

    def fake_gateway(*args: object, **kwargs: object) -> FakeGateway:
        calls.append("gateway")
        return FakeGateway(calls, *args, **kwargs)

    def fake_build_phase4_runtime(**kwargs: Any) -> SimpleNamespace:
        calls.append("build")
        captured.update(kwargs)
        return _fake_runtime()

    monkeypatch.setattr(
        cli,
        "validate_rag_database",
        validate,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "create_existing_rag_database_engine",
        existing_only_engine_factory,
        raising=False,
    )
    monkeypatch.setattr(lm_studio, "LMStudioGateway", fake_gateway)
    monkeypatch.setattr(runtime_module, "build_phase4_runtime", fake_build_phase4_runtime)
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda: object())

    configured = cli._build_configured_runtime()

    try:
        assert calls[:3] == ["validate", "gateway", "build"]
        assert validated_paths == [captured["paths"].database_path]
        assert captured.get("database_engine_factory") is existing_only_engine_factory
    finally:
        configured.close()


def test_shared_factory_validates_database_and_injects_existing_only_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import httpx2

    import aaos.rag.factory as factory

    calls: list[str] = []
    validated_paths: list[Path] = []
    captured: dict[str, Any] = {}

    def validate(database_path: Path) -> None:
        calls.append("validate")
        validated_paths.append(database_path)

    def existing_only_engine_factory(database_path: Path) -> object:
        raise AssertionError(f"fake runtime must not actually open database {database_path}")

    def fake_gateway(*args: object, **kwargs: object) -> FakeGateway:
        calls.append("gateway")
        return FakeGateway(calls, *args, **kwargs)

    def fake_build_phase4_runtime(**kwargs: Any) -> SimpleNamespace:
        calls.append("build")
        captured.update(kwargs)
        return _fake_runtime()

    monkeypatch.setattr(
        factory,
        "validate_rag_database",
        validate,
        raising=False,
    )
    monkeypatch.setattr(
        factory,
        "create_existing_rag_database_engine",
        existing_only_engine_factory,
        raising=False,
    )
    monkeypatch.setattr(factory, "LMStudioGateway", fake_gateway)
    monkeypatch.setattr(factory, "build_phase4_runtime", fake_build_phase4_runtime)
    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda: object())

    configured = factory.build_configured_phase4_runtime(
        data_root=tmp_path.resolve(),
    )

    try:
        assert calls[:3] == ["validate", "gateway", "build"]
        assert validated_paths == [captured["paths"].database_path]
        assert captured.get("database_engine_factory") is existing_only_engine_factory
    finally:
        configured.close()
