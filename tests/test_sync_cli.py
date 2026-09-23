from __future__ import annotations

import sys
from collections.abc import Callable

import pytest


def test_main_dispatches_only_exact_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import cli

    calls: list[str] = []

    def dispatch() -> int:
        calls.append("sync")
        return 0

    monkeypatch.setattr(
        cli,
        "_run_configured_sync_workflow",
        dispatch,
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["aaos", "sync"])

    assert cli.main() == 0
    assert calls == ["sync"]


def test_sync_with_extra_arguments_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import cli

    calls: list[str] = []

    def forbidden() -> int:
        calls.append("sync")
        return 0

    monkeypatch.setattr(
        cli,
        "_run_configured_sync_workflow",
        forbidden,
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["aaos", "sync", "--retry"],
    )

    assert cli.main() == 2
    assert calls == []


def test_configured_sync_wires_existing_command_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aaos import cli
    from aaos import sync as sync_module
    from aaos.documents import cli as documents_cli

    events: list[object] = []

    def moodle() -> int:
        events.append("moodle")
        return 0

    def documents(argv: list[str]) -> int:
        events.append(("documents", tuple(argv)))
        return 0

    def rag(argv: list[str]) -> int:
        events.append(("rag", tuple(argv)))
        return 0

    def workflow(
        *,
        run_moodle_sync: Callable[[], int],
        run_document_ingest: Callable[[], int],
        run_rag_index: Callable[[], int],
    ) -> int:
        events.append("workflow")

        assert run_moodle_sync() == 0
        assert run_document_ingest() == 0
        assert run_rag_index() == 0

        return 0

    monkeypatch.setattr(
        cli,
        "_run_configured_moodle_sync",
        moodle,
    )
    monkeypatch.setattr(
        documents_cli,
        "run_documents_command",
        documents,
    )
    monkeypatch.setattr(
        cli,
        "_run_configured_rag",
        rag,
    )
    monkeypatch.setattr(
        sync_module,
        "run_sync_workflow",
        workflow,
    )

    assert cli._run_configured_sync_workflow() == 0

    assert events == [
        "workflow",
        "moodle",
        ("documents", ("ingest-new",)),
        ("rag", ("index-new",)),
    ]
