"""Pinned query retrieval and deterministic hybrid rank fusion.

SQLite authorization remains the control plane.  Dense and lexical stores may
nominate chunk keys, but only authoritative READY SQLite state can admit a
candidate into the fused result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    DenseIndex,
    DenseSearchHit,
    EmbeddingService,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
)
from aaos.rag.lexical import LexicalSearchHit, LexicalSearchResult, normalize_lexical_text
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


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One deduplicated RRF candidate with channel-rank provenance."""

    chunk_key: str
    dense_rank: int | None
    lexical_rank: int | None
    lexical_document_version_id: int | None
    fused_score: float
    fused_rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_key, str) or not self.chunk_key:
            raise ValueError("chunk_key must be non-blank")
        if self.dense_rank is None and self.lexical_rank is None:
            raise ValueError("at least one component rank is required")
        for name in ("dense_rank", "lexical_rank"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer when present")
        if (self.lexical_rank is None) != (self.lexical_document_version_id is None):
            raise ValueError("lexical rank and document-version identity must appear together")
        if self.lexical_document_version_id is not None and (
            isinstance(self.lexical_document_version_id, bool)
            or not isinstance(self.lexical_document_version_id, int)
            or self.lexical_document_version_id <= 0
        ):
            raise ValueError("lexical_document_version_id must be positive when present")
        if not isinstance(self.fused_score, float) or not isfinite(self.fused_score):
            raise ValueError("fused_score must be finite")
        if self.fused_score <= 0:
            raise ValueError("fused_score must be positive")
        if (
            isinstance(self.fused_rank, bool)
            or not isinstance(self.fused_rank, int)
            or self.fused_rank <= 0
        ):
            raise ValueError("fused_rank must be positive")


def _validate_dense_hits(hits: tuple[DenseSearchHit, ...]) -> None:
    if not isinstance(hits, tuple) or not all(isinstance(hit, DenseSearchHit) for hit in hits):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if tuple(hit.rank for hit in hits) != tuple(range(1, len(hits) + 1)):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if len({hit.chunk_key for hit in hits}) != len(hits):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)


def _validate_lexical_hits(hits: tuple[LexicalSearchHit, ...]) -> None:
    if not isinstance(hits, tuple) or not all(isinstance(hit, LexicalSearchHit) for hit in hits):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if tuple(hit.rank for hit in hits) != tuple(range(1, len(hits) + 1)):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if len({hit.chunk_key for hit in hits}) != len(hits) or any(
        isinstance(hit.document_version_id, bool)
        or not isinstance(hit.document_version_id, int)
        or hit.document_version_id <= 0
        for hit in hits
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)


def reciprocal_rank_fusion(
    dense_hits: tuple[DenseSearchHit, ...],
    lexical_hits: tuple[LexicalSearchHit, ...],
    *,
    rrf_k: int,
) -> tuple[FusedCandidate, ...]:
    """Fuse ranks only; raw distance and BM25 score scales never participate."""
    if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k <= 0:
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    _validate_dense_hits(dense_hits)
    _validate_lexical_hits(lexical_hits)

    components: dict[str, dict[str, int | None]] = {}
    for dense_hit in dense_hits:
        components[dense_hit.chunk_key] = {
            "dense_rank": dense_hit.rank,
            "lexical_rank": None,
            "lexical_document_version_id": None,
        }
    for lexical_hit in lexical_hits:
        values = components.setdefault(
            lexical_hit.chunk_key,
            {
                "dense_rank": None,
                "lexical_rank": None,
                "lexical_document_version_id": None,
            },
        )
        values["lexical_rank"] = lexical_hit.rank
        values["lexical_document_version_id"] = lexical_hit.document_version_id

    scored: list[tuple[str, int | None, int | None, int | None, float]] = []
    for chunk_key, values in components.items():
        dense_rank = values["dense_rank"]
        lexical_rank = values["lexical_rank"]
        score = sum(1 / (rrf_k + rank) for rank in (dense_rank, lexical_rank) if rank is not None)
        scored.append(
            (
                chunk_key,
                dense_rank,
                lexical_rank,
                values["lexical_document_version_id"],
                score,
            )
        )
    scored.sort(
        key=lambda item: (
            -item[4],
            min(rank for rank in (item[1], item[2]) if rank is not None),
            item[0],
        )
    )
    return tuple(
        FusedCandidate(
            chunk_key=chunk_key,
            dense_rank=dense_rank,
            lexical_rank=lexical_rank,
            lexical_document_version_id=lexical_document_version_id,
            fused_score=score,
            fused_rank=fused_rank,
        )
        for fused_rank, (
            chunk_key,
            dense_rank,
            lexical_rank,
            lexical_document_version_id,
            score,
        ) in enumerate(scored, start=1)
    )


def verify_fused_candidates(
    repository: RagRepository,
    snapshot: QuerySnapshot,
    candidates: tuple[FusedCandidate, ...],
) -> tuple[FusedCandidate, ...]:
    """Fail closed unless every fused key resolves inside the frozen universe."""
    if (
        not isinstance(repository, RagRepository)
        or not isinstance(snapshot, QuerySnapshot)
        or not isinstance(candidates, tuple)
        or not all(isinstance(candidate, FusedCandidate) for candidate in candidates)
        or tuple(candidate.fused_rank for candidate in candidates)
        != tuple(range(1, len(candidates) + 1))
        or len({candidate.chunk_key for candidate in candidates}) != len(candidates)
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if not candidates:
        return ()

    try:
        materialized = repository.materialize_snapshot_chunks(
            snapshot, tuple(candidate.chunk_key for candidate in candidates)
        )
        if len(materialized) != len(candidates):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        for candidate, authoritative in zip(candidates, materialized, strict=True):
            if authoritative.chunk.chunk_key != candidate.chunk_key or (
                candidate.lexical_document_version_id is not None
                and candidate.lexical_document_version_id
                != authoritative.document_version.document_version_id
            ):
                raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    except RagOperationalError:
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
    except Exception:
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
    return candidates


class HybridRetriever:
    """Produce independently ranked candidates inside one authorized snapshot.

    Candidate search remains independently inspectable, while
    ``retrieve_fused_candidates`` enforces the complete Task 15 path.
    """

    def __init__(
        self,
        repository: RagRepository,
        embedding_service: EmbeddingService,
        dense_index: DenseIndex,
        *,
        dense_candidate_limit: int = 30,
        lexical_candidate_limit: int = 30,
        rrf_k: int = 60,
        lexical_search: Callable[[str, QuerySnapshot, int], LexicalSearchResult] | None = None,
    ) -> None:
        for value, name in (
            (dense_candidate_limit, "dense_candidate_limit"),
            (lexical_candidate_limit, "lexical_candidate_limit"),
            (rrf_k, "rrf_k"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._repository = repository
        self._embedding_service = embedding_service
        self._dense_index = dense_index
        self._dense_candidate_limit = dense_candidate_limit
        self._lexical_candidate_limit = lexical_candidate_limit
        self._rrf_k = rrf_k
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

    def retrieve_fused_candidates(self, query: RagQuery) -> tuple[FusedCandidate, ...]:
        """Search, fuse, and admit candidates through authoritative SQLite."""
        candidate_set = self.retrieve_candidates(query)
        fused = reciprocal_rank_fusion(
            candidate_set.dense_hits,
            candidate_set.lexical_result.hits,
            rrf_k=self._rrf_k,
        )
        return verify_fused_candidates(self._repository, candidate_set.snapshot, fused)


__all__ = [
    "FusedCandidate",
    "HybridRetriever",
    "QuerySnapshot",
    "QuerySnapshotResolver",
    "RetrievalCandidateSet",
    "normalize_query",
    "reciprocal_rank_fusion",
    "resolve_query_snapshot",
    "verify_fused_candidates",
]
