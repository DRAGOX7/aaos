from __future__ import annotations

from collections.abc import Callable


def _runner(
    events: list[str],
    name: str,
    exit_code: int,
) -> Callable[[], int]:
    def run() -> int:
        events.append(name)
        return exit_code

    return run


def test_sync_workflow_runs_moodle_then_documents_then_rag() -> None:
    from aaos.sync import run_sync_workflow

    events: list[str] = []

    result = run_sync_workflow(
        run_moodle_sync=_runner(events, "moodle", 0),
        run_document_ingest=_runner(events, "documents", 0),
        run_rag_index=_runner(events, "rag", 0),
    )

    assert result == 0
    assert events == ["moodle", "documents", "rag"]


def test_sync_workflow_stops_when_moodle_sync_fails() -> None:
    from aaos.sync import run_sync_workflow

    events: list[str] = []

    result = run_sync_workflow(
        run_moodle_sync=_runner(events, "moodle", 1),
        run_document_ingest=_runner(events, "documents", 0),
        run_rag_index=_runner(events, "rag", 0),
    )

    assert result == 1
    assert events == ["moodle"]


def test_sync_workflow_stops_when_document_ingestion_fails() -> None:
    from aaos.sync import run_sync_workflow

    events: list[str] = []

    result = run_sync_workflow(
        run_moodle_sync=_runner(events, "moodle", 0),
        run_document_ingest=_runner(events, "documents", 1),
        run_rag_index=_runner(events, "rag", 0),
    )

    assert result == 1
    assert events == ["moodle", "documents"]


def test_sync_workflow_propagates_rag_index_failure() -> None:
    from aaos.sync import run_sync_workflow

    events: list[str] = []

    result = run_sync_workflow(
        run_moodle_sync=_runner(events, "moodle", 0),
        run_document_ingest=_runner(events, "documents", 0),
        run_rag_index=_runner(events, "rag", 1),
    )

    assert result == 1
    assert events == ["moodle", "documents", "rag"]
