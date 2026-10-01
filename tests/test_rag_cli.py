"""Tests for the exact, safe Phase 4 RAG CLI surface."""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from aaos.rag.contracts import (
    CourseScope,
    GlobalScope,
    GroundedAnswerRequest,
    GroundedModelOutput,
    HistoricalScope,
    IndexRunResult,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagClaim,
    RagErrorCode,
    RagIndexStatus,
    RagOperationalError,
    RagQuery,
    RenderedRagResponse,
    RetrievedEvidence,
)
from aaos.rag.generation import CitationValidator


def answer_response() -> RenderedRagResponse:
    evidence = RetrievedEvidence(
        "evidence-1",
        "chunk-1",
        "Evidence text",
        None,
        1,
        "Document",
        1,
        (RagChunkSourceSpanValue(1, 1, 0, 12, 0, 12),),
        1,
        None,
        1,
        7,
        2,
    )
    request = GroundedAnswerRequest(
        "grounded question",
        "en",
        7,
        2,
        (evidence,),
        "grounded-v1",
    )
    claim = RagClaim("claim-1", "The answer is grounded.", ("evidence-1",))
    output = GroundedModelOutput(
        RagAnswerStatus.ANSWER,
        "The answer is grounded.",
        "en",
        (claim,),
        7,
        2,
        "grounded-v1",
    )
    return _render_validated(output, request)


def _render_validated(
    output: GroundedModelOutput, request: GroundedAnswerRequest
) -> RenderedRagResponse:
    from aaos.rag.generation import render_rag_answer

    return render_rag_answer(CitationValidator().validate(output, request))


@dataclass
class FakeIndexing:
    index_calls: list[int | None]
    rebuild_calls: int = 0
    status_calls: int = 0
    index_result: IndexRunResult = IndexRunResult("PASS", 7, 2, 3, 0)
    status_result: RagIndexStatus = RagIndexStatus(None, 0, 0, 0, 0, False)
    index_error: Exception | None = None

    def index_new(self, document_version_id: int | None = None) -> IndexRunResult:
        self.index_calls.append(document_version_id)
        if self.index_error is not None:
            raise self.index_error
        return self.index_result

    def rebuild(self) -> IndexRunResult:
        self.rebuild_calls += 1
        return self.index_result

    def status(self) -> RagIndexStatus:
        self.status_calls += 1
        return self.status_result


@dataclass
class FakeService:
    queries: list[RagQuery]
    response: RenderedRagResponse = answer_response()
    error: Exception | None = None

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class FakeRuntime:
    service: FakeService
    indexing: FakeIndexing
    close_error: Exception | None = None
    closed: int = 0

    def close(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def run(
    monkeypatch: pytest.MonkeyPatch,
    runtime: FakeRuntime,
    argv: list[str],
    *,
    question: str = "",
) -> tuple[int, str, str]:
    import aaos.rag.cli as module

    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(module, "_build_configured_runtime", lambda: runtime)
    code = module.run_rag_command(
        argv,
        stdin=io.StringIO(question),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def make_runtime() -> FakeRuntime:
    return FakeRuntime(FakeService([]), FakeIndexing([]))


def test_index_new_forwards_optional_document_version_and_renders_safe_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime()
    code, output, error = run(monkeypatch, runtime, ["index-new", "--document-version-id", "19"])

    assert code == 0
    assert runtime.indexing.index_calls == [19]
    assert runtime.closed == 1
    assert error == ""
    assert output.splitlines() == [
        "RAG_INDEX_NEW",
        "STATUS=PASS",
        "GENERATION_ID=7",
        "CONTENT_REVISION=2",
        "INDEXED_DOCUMENTS=3",
        "NOT_SEARCHABLE_DOCUMENTS=0",
    ]


def test_rebuild_and_status_use_indexing_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = make_runtime()
    runtime.indexing.status_result = RagIndexStatus(
        active_generation=None,
        ready_documents=3,
        partial_documents=1,
        not_searchable_documents=2,
        chunks=8,
        dense_index_healthy=True,
    )

    code, output, error = run(monkeypatch, runtime, ["rebuild"])
    assert code == 0
    assert runtime.indexing.rebuild_calls == 1
    assert runtime.closed == 1
    assert error == ""
    assert "STATUS=PASS" in output

    runtime = make_runtime()
    runtime.indexing.status_result = RagIndexStatus(None, 3, 1, 2, 8, True)
    code, output, error = run(monkeypatch, runtime, ["status"])
    assert code == 0
    assert runtime.indexing.status_calls == 1
    assert runtime.closed == 1
    assert error == ""
    assert output.splitlines() == [
        "RAG_STATUS",
        "ACTIVE_GENERATION=NONE",
        "READY_DOCUMENTS=3",
        "PARTIAL_DOCUMENTS=1",
        "NOT_SEARCHABLE_DOCUMENTS=2",
        "CHUNKS=8",
        "DENSE_INDEX_HEALTHY=YES",
    ]


@pytest.mark.parametrize(
    ("scope_args", "scope_type"),
    [
        (["--course-id", "11"], CourseScope),
        (["--global"], GlobalScope),
        (["--document-version-id", "23"], HistoricalScope),
    ],
)
def test_ask_reads_one_question_and_builds_explicit_scope(
    monkeypatch: pytest.MonkeyPatch,
    scope_args: list[str],
    scope_type: type[object],
) -> None:
    runtime = make_runtime()
    code, output, error = run(
        monkeypatch,
        runtime,
        ["ask", *scope_args],
        question="private question that must not be echoed\n",
    )

    assert code == 0
    assert runtime.closed == 1
    assert error == ""
    assert output
    assert "private question" not in output
    assert runtime.service.queries
    query = runtime.service.queries[0]
    assert isinstance(query, RagQuery)
    assert isinstance(query.scope, scope_type)
    if isinstance(query.scope, CourseScope):
        assert query.scope.course_id == 11
        scope_lines: tuple[str, ...] = ("SCOPE=COURSE", "COURSE_ID=11")
    elif isinstance(query.scope, GlobalScope):
        scope_lines = ("SCOPE=GLOBAL",)
    elif isinstance(query.scope, HistoricalScope):
        assert query.scope.document_version_ids == (23,)
        scope_lines = ("SCOPE=HISTORICAL", "DOCUMENT_VERSION_ID=23")
    else:
        raise AssertionError("unexpected scope")
    assert output.splitlines()[: 2 + len(scope_lines) + 2] == [
        "RAG_ANSWER",
        "STATUS=ANSWER",
        *scope_lines,
        "GENERATION_ID=7",
        "CONTENT_REVISION=2",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["index-new", "--bad"],
        ["index-new", "--document-version-id", "0"],
        ["index-new", "--document-version-id", "1", "--document-version-id", "2"],
        ["rebuild", "extra"],
        ["status", "--global"],
        ["ask"],
        ["ask", "--global", "--course-id", "2"],
        ["ask", "--course-id", "0"],
        ["ask", "--document-version-id", "https://private"],
        ["ask", "--question", "private question"],
    ],
)
def test_invalid_grammar_returns_two_without_constructing_runtime(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    import aaos.rag.cli as module

    built = False

    def builder() -> FakeRuntime:
        nonlocal built
        built = True
        raise AssertionError("invalid CLI input must not build a runtime")

    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(module, "_build_configured_runtime", builder)
    assert module.run_rag_command(argv, stdin=io.StringIO(), stdout=out, stderr=err) == 2
    assert not built
    assert out.getvalue() == ""
    assert "usage: aaos rag" in err.getvalue()


def test_blank_question_is_rejected_without_service_call(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = make_runtime()
    code, output, error = run(monkeypatch, runtime, ["ask", "--global"], question="  \n")
    assert code == 2
    assert runtime.service.queries == []
    assert runtime.closed == 0
    assert output == ""
    assert "RAG_INVALID_INPUT" in error


def test_operational_error_is_fixed_and_runtime_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = make_runtime()
    runtime.indexing.index_error = RagOperationalError(RagErrorCode.RAG_INDEX_LOCKED)
    code, output, error = run(monkeypatch, runtime, ["index-new"])
    assert code == 1
    assert output == ""
    assert error.splitlines() == ["RAG_OPERATION_FAILED", "ERROR_CODE=RAG_INDEX_LOCKED"]
    assert runtime.closed == 1


def test_unexpected_failure_is_sanitized_and_does_not_echo_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime()
    runtime.service.error = RuntimeError("private question secret https://private")
    code, output, error = run(
        monkeypatch,
        runtime,
        ["ask", "--global"],
        question="private question\n",
    )
    assert code == 1
    assert output == ""
    assert error == "RAG_OPERATION_FAILED\n"
    assert "private" not in error
    assert runtime.closed == 1


def test_cleanup_failure_is_safe_and_result_is_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = make_runtime()
    runtime.close_error = RuntimeError("private cleanup detail")
    code, output, error = run(monkeypatch, runtime, ["status"])
    assert code == 1
    assert output == ""
    assert error == "RAG_CLEANUP_FAILED\n"
    assert runtime.closed == 1


def test_main_dispatches_rag_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    import aaos.cli as cli

    calls: list[tuple[str, ...]] = []

    def fake_run(arguments: list[str]) -> int:
        calls.append(tuple(arguments))
        return 7

    import aaos.rag.cli as rag_cli

    monkeypatch.setattr(rag_cli, "run_rag_command", fake_run)
    monkeypatch.setattr(sys, "argv", ["aaos", "rag", "status"])
    assert cli.main() == 7
    assert calls == [("status",)]


def test_cli_module_does_not_construct_runtime_on_import() -> None:
    import importlib

    module = importlib.import_module("aaos.rag.cli")
    assert module is not None


def test_ask_closes_configured_gateway_on_same_event_loop() -> None:
    import asyncio
    import io

    import aaos.rag.cli as module

    class LoopBoundGateway:
        def __init__(self) -> None:
            self.used_loop: asyncio.AbstractEventLoop | None = None
            self.close_calls = 0

        async def mark_used(self) -> None:
            self.used_loop = asyncio.get_running_loop()

        async def aclose(self) -> None:
            self.close_calls += 1
            if asyncio.get_running_loop() is not self.used_loop:
                raise RuntimeError("gateway closed from different event loop")

    class LoopBoundService:
        def __init__(self, gateway: LoopBoundGateway) -> None:
            self._gateway = gateway
            self._delegate = FakeService([])

        async def ask(self, query: RagQuery) -> RenderedRagResponse:
            await self._gateway.mark_used()
            return await self._delegate.ask(query)

    gateway = LoopBoundGateway()
    underlying = FakeRuntime(
        service=LoopBoundService(gateway),  # type: ignore[arg-type]
        indexing=FakeIndexing([]),
    )
    runtime = module._ConfiguredRuntime(underlying, gateway)

    output = io.StringIO()
    error = io.StringIO()

    code = module.run_rag_command(
        ["ask", "--global"],
        build_runtime=lambda: runtime,
        stdin=io.StringIO("What is covered?\n"),
        stdout=output,
        stderr=error,
    )

    assert code == 0
    assert error.getvalue() == ""
    assert output.getvalue().startswith("RAG_ANSWER\n")
    assert underlying.closed == 1
    assert gateway.close_calls == 1


@dataclass
class FakeSummarizer:
    calls: list[tuple[int, str]]
    versions: tuple[int, ...] = (101, 102)
    outcome: str = "READY"

    def published_document_versions(self) -> tuple[int, ...]:
        return self.versions

    async def summarize_document(
        self, document_version_id: int, language: str, *, deadline: float | None = None
    ) -> Any:
        from aaos.rag.summaries import SummaryOutcome, SummaryResult

        self.calls.append((document_version_id, language))
        outcome = SummaryOutcome(self.outcome)
        return SummaryResult(
            outcome,
            answer_response() if outcome is SummaryOutcome.READY else None,
            (document_version_id,),
            dropped_points=1,
        )


@dataclass
class SummarizingRuntime(FakeRuntime):
    summarizer: FakeSummarizer | None = None


def _summarizing_runtime(summarizer: FakeSummarizer | None) -> SummarizingRuntime:
    return SummarizingRuntime(
        service=FakeService([]), indexing=FakeIndexing([]), summarizer=summarizer
    )


def test_summarize_one_document_prints_status_and_the_cited_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarizer = FakeSummarizer([])
    code, out, err = run(
        monkeypatch, _summarizing_runtime(summarizer), ["summarize", "--document-version-id", "101"]
    )

    assert code == 0, err
    assert summarizer.calls == [(101, "en")]
    assert "RAG_SUMMARY\nDOCUMENT_VERSION_ID=101\nSTATUS=READY\nDROPPED_POINTS=1\n" in out
    assert answer_response().rendered_text in out


def test_summarize_all_prepares_every_published_version_in_the_requested_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarizer = FakeSummarizer([], outcome="NO_VERIFIED_POINTS")
    code, out, _err = run(
        monkeypatch, _summarizing_runtime(summarizer), ["summarize", "--all", "--language", "ar"]
    )

    assert code == 0
    assert summarizer.calls == [(101, "ar"), (102, "ar")]
    assert out.count("STATUS=NO_VERIFIED_POINTS") == 2


@pytest.mark.parametrize(
    "argv",
    (
        ["summarize"],
        ["summarize", "--all", "--language", "fr"],
        ["summarize", "--document-version-id", "0"],
        ["summarize", "--all", "--document-version-id", "1"],
    ),
)
def test_summarize_rejects_malformed_arguments(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    code, _out, err = run(monkeypatch, _summarizing_runtime(FakeSummarizer([])), argv)

    assert code == 2
    assert "summarize" in err


class FailingOnceSummarizer(FakeSummarizer):
    async def summarize_document(
        self, document_version_id: int, language: str, *, deadline: float | None = None
    ) -> Any:
        from aaos.rag.contracts import RagErrorCode, RagOperationalError

        if document_version_id == 101:
            self.calls.append((document_version_id, language))
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        return await super().summarize_document(document_version_id, language)


def test_summarize_all_continues_past_a_failed_document_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: one failed document aborted --all and discarded the rest."""
    summarizer = FailingOnceSummarizer([])
    code, out, err = run(monkeypatch, _summarizing_runtime(summarizer), ["summarize", "--all"])

    assert code == 1
    assert summarizer.calls == [(101, "en"), (102, "en")]
    assert "DOCUMENT_VERSION_ID=101\nSTATUS=FAILED\nERROR_CODE=INVALID_STRUCTURED_OUTPUT\n" in out
    assert "DOCUMENT_VERSION_ID=102\nSTATUS=READY\n" in out
    assert "RAG_SUMMARY_INCOMPLETE" in err


def test_summarize_without_a_configured_summarizer_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, _out, err = run(
        monkeypatch, _summarizing_runtime(None), ["summarize", "--document-version-id", "101"]
    )

    assert code == 1
    assert "RAG_OPERATION_FAILED" in err
