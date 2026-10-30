"""Coarse progress stages of one Ask request, for the streaming Ask route.

A stage says only which step of the pipeline is running. It never carries
model text, evidence, or any other payload, so reporting it cannot leak an
answer before evidence verification has finished.

The streaming route installs a reporter in a context variable before it
starts the Ask task; code anywhere below that task calls ``report_ask_stage``.
With no reporter installed (the normal non-streaming route, the CLI, tests),
reporting does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum


class AskStage(StrEnum):
    RECEIVED = "RECEIVED"
    SEARCHING = "SEARCHING"
    CHECKING_COVERAGE = "CHECKING_COVERAGE"
    WRITING = "WRITING"
    VERIFYING = "VERIFYING"


AskStageReporter = Callable[[AskStage], None]

ASK_STAGE_REPORTER: ContextVar[AskStageReporter | None] = ContextVar(
    "aaos_ask_stage_reporter", default=None
)


def report_ask_stage(stage: AskStage) -> None:
    """Tell the streaming route which stage started. Never raises."""

    reporter = ASK_STAGE_REPORTER.get()
    if reporter is None:
        return
    try:
        reporter(stage)
    except Exception:
        logging.getLogger(__name__).warning("Ask progress reporter failed")
