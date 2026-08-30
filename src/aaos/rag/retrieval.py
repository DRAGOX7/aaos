"""Task 14 query normalization and authoritative scope snapshots.

This module contains only query control-plane logic.  Ranking, fusion, and
answer generation are deliberately left to later Phase 4 tasks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    DenseIndex,
    DenseSearchHit,
    EmbeddingService,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
)
from aaos.rag.lexical import LexicalSearchResult, normalize_lexical_text
from aaos.rag.repository import QuerySnapshot, RagRepository


def normalize_query(question: str) -> str:
    """Return a bounded, conservative lexical form of a query."""
    if not isinstance(question, str):
        raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
    if len(question) > MAX_RAG_TEXT_CHARACTERS:
        raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
    normalized = normalize_lexical_text(question)
    if not normalized or len(normalized) > MAX_RAG_TEXT_CHARACTERS:
        raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
    return normalized


class QuerySnapshotResolver:
    """Resolve a typed query into one immutable SQLite-owned snapshot."""

    def __init__(self, repository: RagRepository) -> None:
        self._repository = repository

    def resolve(self, query: RagQuery) -> QuerySnapshot:
        if not isinstance(query, RagQuery):
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        normalized = normalize_query(query.question)
        return self._repository.pin_query_snapshot(
            query.scope,
            normalized_query=normalized,
        )


def resolve_query_snapshot(repository: RagRepository, query: RagQuery) -> QuerySnapshot:
    """Resolve one typed query without exposing sessions or ORM rows."""
    return QuerySnapshotResolver(repository).resolve(query)


@dataclass(frozen=True, slots=True)
class RetrievalCandidateSet:
    """Independent dense and lexical candidates for one pinned snapshot."""

    snapshot: QuerySnapshot
    dense_hits: tuple[DenseSearchHit, ...]
    lexical_result: LexicalSearchResult

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, QuerySnapshot):
            raise ValueError("snapshot must be a QuerySnapshot")
        if not isinstance(self.dense_hits, tuple) or not all(
            isinstance(hit, DenseSearchHit) for hit in self.dense_hits
        ):
            raise ValueError("dense_hits must be a tuple of DenseSearchHit")
        if not isinstance(self.lexical_result, LexicalSearchResult):
            raise ValueError("lexical_result must be a LexicalSearchResult")


class HybridRetriever:
    """Produce independently ranked candidates inside one authorized snapshot.

    Reciprocal-rank fusion and authoritative candidate verification belong to
    Task 15 and are intentionally absent here.
    """

    def __init__(
        self,
        repository: RagRepository,
        embedding_service: EmbeddingService,
        dense_index: DenseIndex,
        *,
        dense_candidate_limit: int = 30,
        lexical_candidate_limit: int = 30,
        lexical_search: Callable[[str, QuerySnapshot, int], LexicalSearchResult] | None = None,
    ) -> None:
        for value, name in (
            (dense_candidate_limit, "dense_candidate_limit"),
            (lexical_candidate_limit, "lexical_candidate_limit"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._repository = repository
        self._embedding_service = embedding_service
        self._dense_index = dense_index
        self._dense_candidate_limit = dense_candidate_limit
        self._lexical_candidate_limit = lexical_candidate_limit
        self._lexical_search = lexical_search

    def retrieve_candidates(self, query: RagQuery) -> RetrievalCandidateSet:
        """Embed and search only the immutable version universe of ``query``."""
        snapshot = QuerySnapshotResolver(self._repository).resolve(query)
        if not snapshot.document_version_ids:
            return RetrievalCandidateSet(
                snapshot=snapshot,
                dense_hits=(),
                lexical_result=LexicalSearchResult(snapshot.normalized_query, ()),
            )

        try:
            try:
                vector = self._embedding_service.embed_query(query.question)
            except RagOperationalError:
                raise
            except Exception:
                raise RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED) from None

            try:
                dense_hits = self._dense_index.search(
                    vector,
                    limit=self._dense_candidate_limit,
                    allowed_document_version_ids=snapshot.document_version_ids,
                )
            except RagOperationalError:
                raise
            except Exception:
                raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from None
        finally:
            try:
                self._embedding_service.release_model()
            except Exception:
                raise RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED) from None

        try:
            if self._lexical_search is None:
                lexical_result = self._repository.lexical_search(
                    snapshot.normalized_query,
                    snapshot,
                    limit=self._lexical_candidate_limit,
                )
            else:
                lexical_result = self._lexical_search(
                    snapshot.normalized_query,
                    snapshot,
                    self._lexical_candidate_limit,
                )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED) from None
        return RetrievalCandidateSet(snapshot, tuple(dense_hits), lexical_result)


__all__ = [
    "HybridRetriever",
    "QuerySnapshot",
    "QuerySnapshotResolver",
    "RetrievalCandidateSet",
    "normalize_query",
    "resolve_query_snapshot",
]
