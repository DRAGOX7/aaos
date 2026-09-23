"""Top-level AAOS synchronization workflow orchestration."""

from __future__ import annotations

from collections.abc import Callable


def run_sync_workflow(
    *,
    run_moodle_sync: Callable[[], int],
    run_document_ingest: Callable[[], int],
    run_rag_index: Callable[[], int],
) -> int:
    """Run sync stages in order and stop at the first failure."""

    if run_moodle_sync() != 0:
        return 1

    if run_document_ingest() != 0:
        return 1

    if run_rag_index() != 0:
        return 1

    return 0


__all__ = ["run_sync_workflow"]
