"""Pinned query retrieval and deterministic hybrid rank fusion.

SQLite authorization remains the control plane.  Dense and lexical stores may
nominate chunk keys, but only authoritative READY SQLite state can admit a
candidate into the fused result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from secrets import token_urlsafe

from aaos.rag.config import RagIndexConfig
from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    CourseScope,
    DenseIndex,
    DenseIndexFactory,
    DenseSearchHit,
    EmbeddingService,
    RagChunkSourceSpanValue,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
    RetrievedEvidence,
)
from aaos.rag.lexical import LexicalSearchHit, LexicalSearchResult, normalize_lexical_text
from aaos.rag.repository import (
    AuthoritativeRetrievalChunk,
    QuerySnapshot,
    RagRepository,
    SourceSpanRecord,
)

_LEXICAL_RESCUE_MAX_RANK = 2


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
    embedding_release_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, QuerySnapshot):
            raise ValueError("snapshot must be a QuerySnapshot")
        if not isinstance(self.dense_hits, tuple) or not all(
            isinstance(hit, DenseSearchHit) for hit in self.dense_hits
        ):
            raise ValueError("dense_hits must be a tuple of DenseSearchHit")
        if not isinstance(self.lexical_result, LexicalSearchResult):
            raise ValueError("lexical_result must be a LexicalSearchResult")
        if not isinstance(self.embedding_release_verified, bool):
            raise ValueError("embedding_release_verified must be a bool")


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


@dataclass(frozen=True, slots=True)
class EvidenceSelectionConfig:
    """Bounded policy for turning verified candidates into prompt evidence."""

    max_chunks: int = 6
    max_tokens: int = 2400
    adjacent_overlap_ratio: float = 0.5

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_chunks, bool)
            or not isinstance(self.max_chunks, int)
            or not 4 <= self.max_chunks <= 8
        ):
            raise ValueError("max_chunks must be between 4 and 8")
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be positive")
        if (
            isinstance(self.adjacent_overlap_ratio, bool)
            or not isinstance(self.adjacent_overlap_ratio, (int, float))
            or not 0 < float(self.adjacent_overlap_ratio) <= 1
        ):
            raise ValueError("adjacent_overlap_ratio must be between zero and one")
        object.__setattr__(self, "adjacent_overlap_ratio", float(self.adjacent_overlap_ratio))

    @classmethod
    def from_index_config(cls, config: RagIndexConfig) -> "EvidenceSelectionConfig":
        if not isinstance(config, RagIndexConfig):
            raise ValueError("config must be a RagIndexConfig")
        return cls(
            max_chunks=config.max_evidence_chunks,
            max_tokens=config.max_evidence_tokens,
        )


def _selection_config(
    config: EvidenceSelectionConfig | RagIndexConfig | None,
) -> EvidenceSelectionConfig:
    if config is None:
        return EvidenceSelectionConfig()
    if isinstance(config, RagIndexConfig):
        return EvidenceSelectionConfig.from_index_config(config)
    if isinstance(config, EvidenceSelectionConfig):
        return config
    raise ValueError("config must be an EvidenceSelectionConfig or RagIndexConfig")


def _span_interval(span: SourceSpanRecord) -> tuple[int, int, int]:
    return span.page_number, span.page_char_start, span.page_char_end


def _overlap_ratio(
    left: AuthoritativeRetrievalChunk,
    right: AuthoritativeRetrievalChunk,
) -> float:
    """Return source-character overlap for adjacent chunks in one version."""
    if left.document_version.document_version_id != right.document_version.document_version_id:
        return 0.0
    if abs(left.chunk.ordinal - right.chunk.ordinal) > 1:
        return 0.0
    left_total = sum(span.page_char_end - span.page_char_start for span in left.source_spans)
    right_total = sum(span.page_char_end - span.page_char_start for span in right.source_spans)
    if left_total <= 0 or right_total <= 0:
        return 0.0
    overlap = 0
    for left_span in left.source_spans:
        left_page, left_start, left_end = _span_interval(left_span)
        for right_span in right.source_spans:
            right_page, right_start, right_end = _span_interval(right_span)
            if left_page == right_page:
                overlap += max(0, min(left_end, right_end) - max(left_start, right_start))
    return overlap / min(left_total, right_total)


def _validate_selection_inputs(
    snapshot: QuerySnapshot,
    candidates: tuple[FusedCandidate, ...],
    authoritative: tuple[AuthoritativeRetrievalChunk, ...],
) -> None:
    if not isinstance(snapshot, QuerySnapshot):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if (
        not isinstance(candidates, tuple)
        or not all(isinstance(candidate, FusedCandidate) for candidate in candidates)
        or tuple(candidate.fused_rank for candidate in candidates)
        != tuple(range(1, len(candidates) + 1))
        or len({candidate.chunk_key for candidate in candidates}) != len(candidates)
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    if (
        not isinstance(authoritative, tuple)
        or not all(isinstance(item, AuthoritativeRetrievalChunk) for item in authoritative)
        or len(authoritative) != len(candidates)
        or len({item.chunk.chunk_key for item in authoritative}) != len(authoritative)
    ):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    by_key = {item.chunk.chunk_key: item for item in authoritative}
    authorized_versions = set(snapshot.document_version_ids)
    for candidate in candidates:
        item = by_key.get(candidate.chunk_key)
        if item is None:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if (
            item.chunk.generation_id != snapshot.generation.generation_id
            or item.document_version.document_version_id not in authorized_versions
            or item.chunk.document_version_id != item.document_version.document_version_id
            or (
                isinstance(snapshot.scope, CourseScope)
                and item.document_version.course_id != snapshot.scope.course_id
            )
            or (
                candidate.lexical_document_version_id is not None
                and candidate.lexical_document_version_id
                != item.document_version.document_version_id
            )
            or not item.source_spans
            or isinstance(item.chunk.token_count, bool)
            or not isinstance(item.chunk.token_count, int)
            or item.chunk.token_count <= 0
            or not isinstance(item.chunk.exact_text, str)
            or not item.chunk.exact_text.strip()
            or any(
                not isinstance(span, SourceSpanRecord)
                or span.rag_chunk_id != item.chunk.chunk_id
                or span.page_char_end <= span.page_char_start
                or span.chunk_char_end <= span.chunk_char_start
                for span in item.source_spans
            )
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)


class EvidenceSelector:
    """Select bounded, authoritative evidence in deterministic rank order."""

    def __init__(
        self,
        config: EvidenceSelectionConfig | RagIndexConfig | None = None,
        *,
        repository: RagRepository,
        evidence_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = _selection_config(config)
        if not isinstance(repository, RagRepository):
            raise ValueError("repository must be a RagRepository")
        self._repository = repository
        self._evidence_id_factory = evidence_id_factory or (lambda: token_urlsafe(18))

    def select(
        self,
        snapshot: QuerySnapshot,
        candidates: tuple[FusedCandidate, ...],
    ) -> tuple[RetrievedEvidence, ...]:
        try:
            authoritative = self._repository.materialize_snapshot_chunks(
                snapshot, tuple(candidate.chunk_key for candidate in candidates)
            )
        except RagOperationalError:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        _validate_selection_inputs(snapshot, candidates, authoritative)
        by_key = {item.chunk.chunk_key: item for item in authoritative}
        ordered = tuple(
            sorted(candidates, key=lambda candidate: (candidate.fused_rank, candidate.chunk_key))
        )
        accepted: list[AuthoritativeRetrievalChunk] = []
        seen_logical: set[tuple[int, str]] = set()
        used_tokens = 0
        all_versions = {
            by_key[candidate.chunk_key].document_version.document_id for candidate in ordered
        }

        def consider(item: AuthoritativeRetrievalChunk) -> bool:
            nonlocal used_tokens
            logical_key = (item.document_version.document_version_id, item.chunk.exact_text.strip())
            if logical_key in seen_logical or any(
                _overlap_ratio(item, previous) >= self._config.adjacent_overlap_ratio
                for previous in accepted
            ):
                return False
            if len(accepted) >= self._config.max_chunks:
                return False
            if used_tokens + item.chunk.token_count > self._config.max_tokens:
                return False
            accepted.append(item)
            seen_logical.add(logical_key)
            used_tokens += item.chunk.token_count
            return True

        for candidate in ordered:
            item = by_key[candidate.chunk_key]
            consider(item)

        # Preserve the normal relevance-first selection while using the final
        # bounded slot for a strong lexical candidate when one was displaced
        # by the fused-rank prefix.  The candidate must still satisfy every
        # existing duplicate, overlap, and token-budget guard.
        if len(accepted) == self._config.max_chunks and accepted:
            retained = accepted[:-1]
            retained_keys = {item.chunk.chunk_key for item in retained}
            retained_logical = {
                (item.document_version.document_version_id, item.chunk.exact_text.strip())
                for item in retained
            }
            retained_tokens = sum(item.chunk.token_count for item in retained)
            rescue: AuthoritativeRetrievalChunk | None = None
            rescue_candidate: FusedCandidate | None = None
            for candidate in sorted(
                ordered,
                key=lambda candidate: (
                    candidate.lexical_rank
                    if candidate.lexical_rank is not None
                    else self._config.max_chunks + 1,
                    candidate.fused_rank,
                    candidate.chunk_key,
                ),
            ):
                if (
                    candidate.chunk_key in retained_keys
                    or candidate.lexical_rank is None
                    or candidate.lexical_rank > _LEXICAL_RESCUE_MAX_RANK
                ):
                    continue
                item = by_key[candidate.chunk_key]
                logical_key = (
                    item.document_version.document_version_id,
                    item.chunk.exact_text.strip(),
                )
                if logical_key in retained_logical or any(
                    _overlap_ratio(item, previous) >= self._config.adjacent_overlap_ratio
                    for previous in retained
                ):
                    continue
                if retained_tokens + item.chunk.token_count > self._config.max_tokens:
                    continue
                rescue = item
                rescue_candidate = candidate
                break
            if rescue is not None and rescue_candidate is not None:
                accepted = [*retained, rescue]
                accepted.sort(
                    key=lambda item: next(
                        candidate.fused_rank
                        for candidate in ordered
                        if candidate.chunk_key == item.chunk.chunk_key
                    )
                )
                seen_logical = {
                    (item.document_version.document_version_id, item.chunk.exact_text.strip())
                    for item in accepted
                }
                used_tokens = sum(item.chunk.token_count for item in accepted)

        # Relevance remains primary, but when the budget would otherwise be
        # monopolized by one document, replace its lowest-ranked selection with
        # the best valid candidate from another document.  The final sort keeps
        # the fused rank as the observable deterministic order.
        selected_versions = {item.document_version.document_id for item in accepted}
        if len(selected_versions) == 1 and len(all_versions) > 1 and accepted:
            selected_keys = {item.chunk.chunk_key for item in accepted}
            replacement: AuthoritativeRetrievalChunk | None = None
            replacement_candidate: FusedCandidate | None = None
            for candidate in ordered:
                if candidate.chunk_key in selected_keys:
                    continue
                item = by_key[candidate.chunk_key]
                if item.document_version.document_id in selected_versions:
                    continue
                logical_key = (
                    item.document_version.document_version_id,
                    item.chunk.exact_text.strip(),
                )
                if logical_key in seen_logical or any(
                    _overlap_ratio(item, previous) >= self._config.adjacent_overlap_ratio
                    for previous in accepted[:-1]
                ):
                    continue
                if (
                    used_tokens - accepted[-1].chunk.token_count + item.chunk.token_count
                    > self._config.max_tokens
                ):
                    continue
                replacement = item
                replacement_candidate = candidate
                break
            if replacement is not None and replacement_candidate is not None:
                removed = accepted.pop()
                used_tokens -= removed.chunk.token_count
                seen_logical.discard(
                    (removed.document_version.document_version_id, removed.chunk.exact_text.strip())
                )
                accepted.append(replacement)
                seen_logical.add(
                    (
                        replacement.document_version.document_version_id,
                        replacement.chunk.exact_text.strip(),
                    )
                )
                used_tokens += replacement.chunk.token_count
                accepted.sort(
                    key=lambda item: next(
                        candidate.fused_rank
                        for candidate in ordered
                        if candidate.chunk_key == item.chunk.chunk_key
                    )
                )

        candidate_by_key = {candidate.chunk_key: candidate for candidate in ordered}
        evidence: list[RetrievedEvidence] = []
        used_ids: set[str] = set()
        for item in accepted:
            candidate = candidate_by_key[item.chunk.chunk_key]
            try:
                evidence_id = self._evidence_id_factory()
            except Exception:
                raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
            if (
                not isinstance(evidence_id, str)
                or not evidence_id.strip()
                or evidence_id.strip() == item.chunk.chunk_key.strip()
                or evidence_id.strip() in used_ids
            ):
                raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
            evidence_id = evidence_id.strip()
            used_ids.add(evidence_id)
            try:
                evidence.append(
                    RetrievedEvidence(
                        evidence_id=evidence_id,
                        chunk_key=item.chunk.chunk_key,
                        exact_text=item.chunk.exact_text,
                        heading=item.chunk.heading,
                        document_version_id=item.document_version.document_version_id,
                        document_title=item.document_version.source_filename,
                        course_id=item.document_version.course_id,
                        source_spans=tuple(
                            RagChunkSourceSpanValue(
                                document_page_id=span.document_page_id,
                                page_number=span.page_number,
                                source_char_start=span.page_char_start,
                                source_char_end=span.page_char_end,
                                chunk_char_start=span.chunk_char_start,
                                chunk_char_end=span.chunk_char_end,
                            )
                            for span in item.source_spans
                        ),
                        dense_rank=candidate.dense_rank,
                        lexical_rank=candidate.lexical_rank,
                        fused_rank=candidate.fused_rank,
                        generation_id=snapshot.generation.generation_id,
                        content_revision=snapshot.generation.content_revision,
                        token_count=item.chunk.token_count,
                    )
                )
            except (TypeError, ValueError):
                raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        return tuple(evidence)


class SufficiencyStatus(StrEnum):
    """Stable labels for the deterministic evidence sufficiency gate."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class SufficiencyDecision:
    status: SufficiencyStatus
    evidence_count: int
    token_count: int
    policy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SufficiencyStatus):
            raise ValueError("status must be a SufficiencyStatus")
        if (
            isinstance(self.evidence_count, bool)
            or not isinstance(self.evidence_count, int)
            or self.evidence_count < 0
        ):
            raise ValueError("evidence_count must be non-negative")
        if (
            isinstance(self.token_count, bool)
            or not isinstance(self.token_count, int)
            or self.token_count < 0
        ):
            raise ValueError("token_count must be non-negative")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version must be non-blank")
        object.__setattr__(self, "policy_version", self.policy_version.strip())


class EvidenceSufficiencyGate:
    """Pure, conservative gate; it never consults a model or external state."""

    def __init__(
        self,
        policy_version: str | RagIndexConfig = "conservative-v1",
        *,
        min_chunks: int | None = None,
        min_tokens: int | None = None,
        max_fused_rank: int | None = None,
    ) -> None:
        if isinstance(policy_version, RagIndexConfig):
            index_config = policy_version
            policy_version = index_config.sufficiency_policy_version
            min_chunks = index_config.sufficiency_min_chunks if min_chunks is None else min_chunks
            min_tokens = index_config.sufficiency_min_tokens if min_tokens is None else min_tokens
            max_fused_rank = (
                index_config.sufficiency_max_fused_rank
                if max_fused_rank is None
                else max_fused_rank
            )
        else:
            min_chunks = 2 if min_chunks is None else min_chunks
            min_tokens = 32 if min_tokens is None else min_tokens
            max_fused_rank = 30 if max_fused_rank is None else max_fused_rank
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ValueError("policy_version must be non-blank")
        if isinstance(min_chunks, bool) or not isinstance(min_chunks, int) or min_chunks <= 0:
            raise ValueError("min_chunks must be positive")
        if isinstance(min_tokens, bool) or not isinstance(min_tokens, int) or min_tokens <= 0:
            raise ValueError("min_tokens must be positive")
        if (
            isinstance(max_fused_rank, bool)
            or not isinstance(max_fused_rank, int)
            or max_fused_rank <= 0
        ):
            raise ValueError("max_fused_rank must be positive")
        self._policy_version = policy_version.strip()
        self._min_chunks = min_chunks
        self._min_tokens = min_tokens
        self._max_fused_rank = max_fused_rank

    def evaluate(
        self,
        evidence: tuple[RetrievedEvidence, ...],
        *,
        snapshot: QuerySnapshot | None = None,
    ) -> SufficiencyDecision:
        if not isinstance(evidence, tuple) or not all(
            isinstance(item, RetrievedEvidence) for item in evidence
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if len({item.evidence_id for item in evidence}) != len(evidence) or len(
            {item.chunk_key for item in evidence}
        ) != len(evidence):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if len({(item.generation_id, item.content_revision) for item in evidence}) > 1:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if any(item.dense_rank is None and item.lexical_rank is None for item in evidence):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        fused_ranks = tuple(item.fused_rank for item in evidence)
        if fused_ranks != tuple(sorted(set(fused_ranks))) or any(
            rank > self._max_fused_rank for rank in fused_ranks
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if not isinstance(snapshot, QuerySnapshot):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if any(
            item.generation_id != snapshot.generation.generation_id
            or item.content_revision != snapshot.generation.content_revision
            or item.document_version_id not in snapshot.document_version_ids
            or (
                isinstance(snapshot.scope, CourseScope)
                and item.course_id != snapshot.scope.course_id
            )
            for item in evidence
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        token_count = sum(item.token_count for item in evidence)
        if not evidence:
            status = SufficiencyStatus.UNSUPPORTED
        elif len(evidence) >= self._min_chunks and token_count >= self._min_tokens:
            status = SufficiencyStatus.SUPPORTED
        else:
            status = SufficiencyStatus.PARTIALLY_SUPPORTED
        return SufficiencyDecision(status, len(evidence), token_count, self._policy_version)


class HybridRetriever:
    """Produce independently ranked candidates inside one authorized snapshot.

    Candidate search remains independently inspectable, while
    ``retrieve_fused_candidates`` enforces the complete Task 15 path.
    """

    def __init__(
        self,
        repository: RagRepository,
        embedding_service: EmbeddingService,
        dense_index_factory: DenseIndexFactory,
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
        if not callable(getattr(dense_index_factory, "open", None)):
            raise ValueError("dense_index_factory must provide open")
        self._dense_index_factory = dense_index_factory
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
                embedding_release_verified=True,
            )

        dense_index: DenseIndex | None = None
        primary_error: RagOperationalError | None = None
        cleanup_error: RagOperationalError | None = None
        dense_hits: tuple[DenseSearchHit, ...] = ()
        try:
            try:
                vector = self._embedding_service.embed_query(query.question)
            except RagOperationalError:
                raise
            except Exception:
                raise RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED) from None

            try:
                dense_index = self._dense_index_factory.open(snapshot.generation)
                if not callable(getattr(dense_index, "search", None)):
                    raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)
                if not callable(getattr(dense_index, "close", None)):
                    raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)
                dense_hits = dense_index.search(
                    vector,
                    limit=self._dense_candidate_limit,
                    allowed_document_version_ids=snapshot.document_version_ids,
                )
            except RagOperationalError:
                raise
            except Exception:
                raise RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE) from None
        except RagOperationalError as error:
            primary_error = error
        finally:
            if dense_index is not None:
                try:
                    dense_index.close()
                except Exception:
                    cleanup_error = RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)
            try:
                self._embedding_service.release_model()
            except Exception:
                if cleanup_error is None:
                    cleanup_error = RagOperationalError(RagErrorCode.QUERY_EMBEDDING_FAILED)

        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error

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
        return RetrievalCandidateSet(
            snapshot,
            tuple(dense_hits),
            lexical_result,
            embedding_release_verified=True,
        )

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
    "EvidenceSelectionConfig",
    "EvidenceSelector",
    "EvidenceSufficiencyGate",
    "SufficiencyDecision",
    "SufficiencyStatus",
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
