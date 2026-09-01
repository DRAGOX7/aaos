"""Conservative lexical normalization and SQLite FTS5 search primitives.

SQLite remains authoritative for chunk text and provenance.  This module only
builds the derived normalized representation and translates FTS5 rows back to
typed candidate values; callers must provide an already-authorized query
snapshot.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .contracts import RagErrorCode, RagOperationalError

LEXICAL_NORMALIZER_VERSION = "unicode-v1"
_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


class _GenerationLike(Protocol):
    @property
    def generation_id(self) -> int: ...

    @property
    def content_revision(self) -> int: ...


class QuerySnapshotLike(Protocol):
    @property
    def generation(self) -> _GenerationLike: ...

    @property
    def document_version_ids(self) -> tuple[int, ...]: ...


@dataclass(frozen=True, slots=True)
class LexicalSearchHit:
    """A ranked candidate whose identity must be resolved through SQLite."""

    chunk_key: str
    document_version_id: int
    rank: int
    bm25_score: float

    @property
    def score(self) -> float:
        """Expose BM25 under the generic candidate score name."""
        return self.bm25_score


@dataclass(frozen=True, slots=True)
class LexicalSearchResult:
    """Normalized query and typed lexical candidates."""

    normalized_query: str
    hits: tuple[LexicalSearchHit, ...]


def normalize_lexical_text(text_value: str) -> str:
    """Normalize width, case, and whitespace without semantic rewriting."""
    if not isinstance(text_value, str):
        raise ValueError("text must be a string")
    normalized = unicodedata.normalize("NFKC", text_value).casefold()
    return " ".join(normalized.split())


def _fts_match_expression(normalized_query: str) -> str:
    """Build a parameterized FTS expression from normalized word tokens."""
    words = tuple(
        dict.fromkeys(match.group(0) for match in _WORD_PATTERN.finditer(normalized_query))
    )
    if not words:
        return ""
    return " OR ".join(f'"{word.replace(chr(34), chr(34) * 2)}"' for word in words)


def insert_fts_row(
    session: Session,
    *,
    chunk_id: int,
    exact_text: str,
    generation_id: int,
    document_version_id: int,
) -> None:
    """Insert one derived FTS row; callers place it in the chunk transaction."""
    normalized_text = normalize_lexical_text(exact_text)
    if not normalized_text:
        raise RagOperationalError(RagErrorCode.FTS_INDEX_FAILED)
    try:
        session.execute(
            text(
                "INSERT INTO rag_chunks_fts "
                "(rowid, normalized_text, generation_id, document_version_id) "
                "VALUES (:chunk_id, :normalized_text, :generation_id, :document_version_id)"
            ),
            {
                "chunk_id": chunk_id,
                "normalized_text": normalized_text,
                "generation_id": generation_id,
                "document_version_id": document_version_id,
            },
        )
    except SQLAlchemyError as error:
        raise RagOperationalError(RagErrorCode.FTS_INDEX_FAILED) from error


def delete_fts_row(session: Session, *, chunk_id: int) -> None:
    """Delete one derived FTS row; missing rows are repaired idempotently."""
    try:
        session.execute(
            text("DELETE FROM rag_chunks_fts WHERE rowid = :chunk_id"),
            {"chunk_id": chunk_id},
        )
    except SQLAlchemyError as error:
        raise RagOperationalError(RagErrorCode.FTS_INDEX_FAILED) from error


def rebuild_fts_rows(
    session: Session,
    *,
    generation_id: int,
    rows: tuple[tuple[int, str, int], ...],
) -> int:
    """Replace one generation's derived rows from authoritative chunk values."""
    try:
        session.execute(
            text("DELETE FROM rag_chunks_fts WHERE generation_id = :generation_id"),
            {"generation_id": generation_id},
        )
    except SQLAlchemyError as error:
        raise RagOperationalError(RagErrorCode.FTS_INDEX_FAILED) from error
    for chunk_id, exact_text, document_version_id in rows:
        insert_fts_row(
            session,
            chunk_id=chunk_id,
            exact_text=exact_text,
            generation_id=generation_id,
            document_version_id=document_version_id,
        )
    return len(rows)


def search_fts(
    session: Session,
    query: str,
    snapshot: QuerySnapshotLike,
    *,
    limit: int,
) -> LexicalSearchResult:
    """Search FTS5 with BM25 and resolve candidates through authoritative rows."""
    if not isinstance(query, str):
        raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED)
    try:
        generation_id = snapshot.generation.generation_id
        document_version_ids = snapshot.document_version_ids
    except AttributeError as error:
        raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED) from error
    if (
        isinstance(generation_id, bool)
        or not isinstance(generation_id, int)
        or generation_id <= 0
        or not isinstance(document_version_ids, tuple)
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in document_version_ids
        )
    ):
        raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED)

    normalized_query = normalize_lexical_text(query)
    match_expression = _fts_match_expression(normalized_query)
    if not match_expression or not document_version_ids:
        return LexicalSearchResult(normalized_query=normalized_query, hits=())

    placeholders = ", ".join(f":version_{index}" for index in range(len(document_version_ids)))
    parameters: dict[str, Any] = {
        "generation_id": generation_id,
        "match_expression": match_expression,
        "limit": limit,
    }
    parameters.update(
        {f"version_{index}": value for index, value in enumerate(document_version_ids)}
    )
    try:
        rows = session.execute(
            text(
                "SELECT c.chunk_key, c.document_version_id, "
                "bm25(rag_chunks_fts) AS bm25_score "
                "FROM rag_chunks_fts "
                "JOIN rag_chunks AS c ON c.id = rag_chunks_fts.rowid "
                "JOIN rag_document_indexes AS di "
                "  ON di.generation_id = c.generation_id "
                " AND di.document_version_id = c.document_version_id "
                " AND di.status = 'READY' "
                "WHERE rag_chunks_fts MATCH :match_expression "
                "  AND c.generation_id = :generation_id "
                "  AND rag_chunks_fts.generation_id = c.generation_id "
                "  AND rag_chunks_fts.document_version_id = c.document_version_id "
                f"  AND c.document_version_id IN ({placeholders}) "
                "ORDER BY bm25(rag_chunks_fts) ASC, c.chunk_key ASC "
                "LIMIT :limit"
            ),
            parameters,
        ).all()
    except SQLAlchemyError as error:
        raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED) from error

    hits = tuple(
        LexicalSearchHit(
            chunk_key=str(row.chunk_key),
            document_version_id=int(row.document_version_id),
            rank=index,
            bm25_score=float(row.bm25_score),
        )
        for index, row in enumerate(rows, start=1)
    )
    return LexicalSearchResult(normalized_query=normalized_query, hits=hits)


__all__ = [
    "LEXICAL_NORMALIZER_VERSION",
    "LexicalSearchHit",
    "LexicalSearchResult",
    "QuerySnapshotLike",
    "delete_fts_row",
    "insert_fts_row",
    "normalize_lexical_text",
    "rebuild_fts_rows",
    "search_fts",
]
