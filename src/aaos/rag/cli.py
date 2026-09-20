"""Exact, privacy-safe command surface for the Phase 4 local RAG runtime."""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from .config import RagIndexConfig, resolve_rag_runtime_paths
from .contracts import (
    CourseScope,
    GlobalScope,
    HistoricalScope,
    IndexRunResult,
    RagAnswerStatus,
    RagErrorCode,
    RagGenerationIdentity,
    RagIndexStatus,
    RagOperationalError,
    RagQuery,
    RagScope,
    RenderedRagResponse,
)
from .database import (
    create_existing_rag_database_engine,
    validate_rag_database,
)

_USAGE = (
    "usage: aaos rag index-new [--document-version-id ID] | rag rebuild | rag status | "
    "rag ask (--course-id ID | --global | --document-version-id ID)"
)
_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
_INDEX_STATUSES = frozenset({"PASS", "NO_OP"})


class _Runtime(Protocol):
    service: _Service
    indexing: _Indexing

    def close(self) -> None: ...


class _Indexing(Protocol):
    def index_new(self, document_version_id: int | None = None) -> IndexRunResult: ...

    def rebuild(self) -> IndexRunResult: ...

    def status(self) -> RagIndexStatus: ...


class _Service(Protocol):
    async def ask(self, query: RagQuery) -> RenderedRagResponse: ...


class _ConfiguredRuntime:
    """Own the configured runtime and its separately-owned async gateway."""

    def __init__(self, runtime: _Runtime, gateway: object) -> None:
        self.service = runtime.service
        self.indexing = runtime.indexing
        self._runtime = runtime
        self._gateway = gateway
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: Exception | None = None
        try:
            self._runtime.close()
        except Exception as error:
            failure = error
        try:
            asyncio.run(self._close_gateway())
        except Exception as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    async def _close_gateway(self) -> None:
        closer = getattr(self._gateway, "aclose", None)
        if not callable(closer):
            raise RuntimeError("gateway close unavailable")
        result = closer()
        if inspect.isawaitable(result):
            await result


def run_rag_command(
    argv: Sequence[str],
    *,
    build_runtime: Callable[[], _Runtime] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Parse and execute one exact Phase 4 command."""
    parsed = _parse_arguments(argv)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    if parsed is None:
        print(_USAGE, file=errors)
        return 2

    if parsed.command == "ask":
        try:
            question = _read_question(stdin or sys.stdin)
        except (OSError, TypeError, ValueError):
            print("RAG_INVALID_INPUT", file=errors)
            return 2
    else:
        question = None

    runtime: _Runtime | None = None
    rendered_lines: tuple[str, ...] = ()
    operational_code: str | None = None
    operation_failed = False
    cleanup_failed = False
    try:
        runtime = (build_runtime or _build_configured_runtime)()
        _validate_runtime(runtime)
        if parsed.command == "index-new":
            result = _indexing(runtime).index_new(parsed.document_version_id)
            rendered_lines = _render_index_result("RAG_INDEX_NEW", result)
        elif parsed.command == "rebuild":
            result = _indexing(runtime).rebuild()
            rendered_lines = _render_index_result("RAG_REBUILD", result)
        elif parsed.command == "status":
            rendered_lines = _render_status(_indexing(runtime).status())
        else:
            assert question is not None
            assert parsed.scope is not None
            response = asyncio.run(_service(runtime).ask(RagQuery(question, parsed.scope)))
            rendered_lines = _render_answer(response, parsed.scope)
    except RagOperationalError as error:
        operation_failed = True
        operational_code = error.code.value
    except (OSError, TypeError, ValueError, RuntimeError, AttributeError):
        operation_failed = True
    except Exception:
        operation_failed = True
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                cleanup_failed = True

    if operation_failed:
        print("RAG_OPERATION_FAILED", file=errors)
        if operational_code is not None:
            print(f"ERROR_CODE={operational_code}", file=errors)
        return 1
    if cleanup_failed:
        print("RAG_CLEANUP_FAILED", file=errors)
        return 1
    for line in rendered_lines:
        print(line, file=output)
    return 0


def _parse_arguments(argv: Sequence[str]) -> _ParsedArguments | None:
    arguments = tuple(argv)
    if not arguments:
        return None
    command = arguments[0]
    if command == "index-new":
        if len(arguments) == 1:
            return _ParsedArguments(command, None, None)
        if len(arguments) == 3 and arguments[1] == "--document-version-id":
            document_version_id = _parse_positive_id(arguments[2])
            if document_version_id is not None:
                return _ParsedArguments(command, document_version_id, None)
        return None
    if command in {"rebuild", "status"}:
        return _ParsedArguments(command, None, None) if len(arguments) == 1 else None
    if command != "ask":
        return None
    if len(arguments) == 2 and arguments[1] == "--global":
        return _ParsedArguments(command, None, GlobalScope())
    if len(arguments) == 3 and arguments[1] in {"--course-id", "--document-version-id"}:
        identifier = _parse_positive_id(arguments[2])
        if identifier is None:
            return None
        if arguments[1] == "--course-id":
            return _ParsedArguments(command, None, CourseScope(identifier))
        return _ParsedArguments(command, None, HistoricalScope((identifier,)))
    return None


def _parse_positive_id(value: object) -> int | None:
    if not isinstance(value, str) or _POSITIVE_DECIMAL.fullmatch(value) is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _read_question(stream: TextIO) -> str:
    question = stream.readline()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be nonblank")
    return question.strip()


def _validate_runtime(runtime: object) -> None:
    if runtime is None or not callable(getattr(runtime, "close", None)):
        raise ValueError("runtime is invalid")
    indexing = getattr(runtime, "indexing", None)
    service = getattr(runtime, "service", None)
    for method in ("index_new", "rebuild", "status"):
        if not callable(getattr(indexing, method, None)):
            raise ValueError("indexing boundary is invalid")
    if not callable(getattr(service, "ask", None)):
        raise ValueError("service boundary is invalid")


def _indexing(runtime: _Runtime) -> _Indexing:
    return runtime.indexing


def _service(runtime: _Runtime) -> _Service:
    return runtime.service


def _render_index_result(header: str, result: object) -> tuple[str, ...]:
    if type(result) is not IndexRunResult or result.safe_status not in _INDEX_STATUSES:
        raise ValueError("invalid index result")
    return (
        header,
        f"STATUS={result.safe_status}",
        f"GENERATION_ID={result.generation_id}",
        f"CONTENT_REVISION={result.content_revision}",
        f"INDEXED_DOCUMENTS={result.indexed_documents}",
        f"NOT_SEARCHABLE_DOCUMENTS={result.not_searchable_documents}",
    )


def _render_status(result: object) -> tuple[str, ...]:
    if type(result) is not RagIndexStatus:
        raise ValueError("invalid status result")
    active = result.active_generation
    lines = ["RAG_STATUS"]
    if active is None:
        lines.append("ACTIVE_GENERATION=NONE")
    elif type(active) is RagGenerationIdentity:
        lines.extend(
            (
                "ACTIVE_GENERATION=PASS",
                f"GENERATION_ID={active.generation_id}",
                f"CONTENT_REVISION={active.content_revision}",
            )
        )
    else:
        raise ValueError("invalid active generation")
    lines.extend(
        (
            f"READY_DOCUMENTS={result.ready_documents}",
            f"PARTIAL_DOCUMENTS={result.partial_documents}",
            f"NOT_SEARCHABLE_DOCUMENTS={result.not_searchable_documents}",
            f"CHUNKS={result.chunks}",
            f"DENSE_INDEX_HEALTHY={'YES' if result.dense_index_healthy else 'NO'}",
        )
    )
    return tuple(lines)


def _render_answer(response: object, scope: RagScope | None) -> tuple[str, ...]:
    if type(response) is not RenderedRagResponse:
        raise ValueError("invalid answer response")
    if not isinstance(scope, (CourseScope, GlobalScope, HistoricalScope)):
        raise ValueError("invalid answer scope")
    if response.answer.status not in {
        RagAnswerStatus.ANSWER,
        RagAnswerStatus.PARTIAL_ANSWER,
        RagAnswerStatus.INSUFFICIENT_EVIDENCE,
    }:
        raise ValueError("invalid answer status")
    if not isinstance(response.rendered_text, str) or not response.rendered_text.strip():
        raise ValueError("answer text is blank")
    lines = [
        "RAG_ANSWER",
        f"STATUS={response.answer.status.value}",
        *_render_scope(scope),
        f"GENERATION_ID={response.answer.generation_id}",
        f"CONTENT_REVISION={response.answer.content_revision}",
    ]
    lines.extend(response.rendered_text.splitlines())
    return tuple(lines)


def _render_scope(scope: RagScope) -> tuple[str, ...]:
    if isinstance(scope, CourseScope):
        return ("SCOPE=COURSE", f"COURSE_ID={scope.course_id}")
    if isinstance(scope, GlobalScope):
        return ("SCOPE=GLOBAL",)
    if isinstance(scope, HistoricalScope) and len(scope.document_version_ids) == 1:
        return ("SCOPE=HISTORICAL", f"DOCUMENT_VERSION_ID={scope.document_version_ids[0]}")
    raise ValueError("historical scope must contain one document version")


def _build_configured_runtime() -> _ConfiguredRuntime:
    """Create the configured runtime only after an explicit CLI invocation."""
    import httpx2

    from aaos.config import DEFAULT_SETTINGS
    from aaos.inference.lm_studio import LMStudioGateway
    from aaos.rag.embeddings import (
        BGE_M3_DIMENSIONS,
        BGE_M3_MODEL_IDENTIFIER,
        BGE_M3_MODEL_REVISION,
    )
    from aaos.rag.runtime import build_phase4_runtime

    config = RagIndexConfig(
        chunk_target_tokens=400,
        chunk_preferred_min_tokens=200,
        chunk_preferred_max_tokens=600,
        chunk_hard_max_tokens=800,
        chunk_overlap_tokens=80,
        dense_candidate_limit=30,
        lexical_candidate_limit=30,
        rrf_k=60,
        max_evidence_chunks=6,
        max_evidence_tokens=2400,
        chunker_version="heading-page-v1",
        lexical_normalizer_version="unicode-v1",
        sufficiency_policy_version="conservative-v1",
        embedding_model_identifier=BGE_M3_MODEL_IDENTIFIER,
        embedding_model_revision=BGE_M3_MODEL_REVISION,
        embedding_dimensions=BGE_M3_DIMENSIONS,
    )
    paths = resolve_rag_runtime_paths(Path("data").resolve())
    validate_rag_database(paths.database_path)

    gateway = LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())
    try:
        runtime = build_phase4_runtime(
            paths=paths,
            config=config,
            gateway=gateway,
            database_engine_factory=create_existing_rag_database_engine,
        )
    except Exception:
        try:
            asyncio.run(gateway.aclose())
        except Exception as cleanup_error:
            raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from cleanup_error
        raise
    return _ConfiguredRuntime(runtime, gateway)


@dataclass(frozen=True, slots=True)
class _ParsedArguments:
    command: str
    document_version_id: int | None
    scope: CourseScope | GlobalScope | HistoricalScope | None


__all__ = ["run_rag_command"]
