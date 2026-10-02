"""Verified, cited document and course summaries built from Phase 4 parts (Phase I3).

A document is split into consecutive sections of its indexed chunks in reading
order. Each section is summarized by the existing grounded answer generator,
every resulting point is checked by the pinned claim-entailment verifier against
the evidence it cites, and unverified points are dropped and counted. The final
summary is a deterministic selection of verified points (no model reduce call),
validated as one answer by the existing citation validator and rendered by the
existing renderer, so it is an ordinary trusted ``RenderedRagResponse``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from aaos.inference.nli import (
    NliClassification,
    NliDecision,
    NliInferenceError,
    NliInputOverflowError,
    NliModelUnavailableError,
)

from .contracts import (
    EntailmentResult,
    GlobalScope,
    GroundedAnswerRequest,
    HistoricalScope,
    RagAnswerStatus,
    RagClaim,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
    RagQuery,
    RagScope,
    RenderedRagResponse,
    RetrievedEvidence,
)
from .generation import CitationValidator, GroundedModelOutput, render_rag_answer
from .repository import AuthoritativeRetrievalChunk, ChunkRecord, QuerySnapshot
from .retrieval import SufficiencyDecision, SufficiencyStatus, evidence_from_authoritative_chunk
from .summary_contracts import (
    MAX_SUMMARY_CHUNK_KEYS_PER_POINT,
    MAX_SUMMARY_POINTS,
    SUMMARY_LANGUAGES,
    StoredDocumentSummary,
    SummaryCacheKey,
    SummaryPoint,
)

SUMMARY_POLICY_VERSION = "summary-v1"
MAX_SECTION_TOKENS = 2000
MAX_SECTION_CHUNKS = 6
MAX_COURSE_DOCUMENTS = 8
# Worst case for one section: a generation call (60 s) plus a schema repair
# would exceed this, so a section only starts with this much time left.
SECTION_TIME_RESERVE_SECONDS = 150.0

_SECTION_QUESTIONS = {
    "en": "Summarize the main points of these excerpts.",
    "ar": "لخّص النقاط الرئيسية في هذه المقتطفات.",
}
_SUMMARY_QUESTIONS = {
    "en": "Summary of the selected course material.",
    "ar": "ملخص مواد المساق المختارة.",
}


class SummaryRepositoryPort(Protocol):
    def pin_query_snapshot(
        self, scope: RagScope, *, normalized_query: str = ""
    ) -> QuerySnapshot: ...

    def list_chunks(
        self, identity: RagGenerationIdentity, document_version_id: int | None = None
    ) -> tuple[ChunkRecord, ...]: ...

    def materialize_snapshot_chunks(
        self, snapshot: QuerySnapshot, chunk_keys: tuple[str, ...]
    ) -> tuple[AuthoritativeRetrievalChunk, ...]: ...

    def get_document_summary(self, key: SummaryCacheKey) -> StoredDocumentSummary | None: ...

    def put_document_summary(self, summary: StoredDocumentSummary) -> None: ...


class SummaryGeneratorPort(Protocol):
    async def generate(
        self, request: GroundedAnswerRequest, sufficiency: SufficiencyDecision
    ) -> GroundedModelOutput: ...


class ClaimVerifierPort(Protocol):
    async def verify(
        self, claim: RagClaim, *, evidence: tuple[RetrievedEvidence, ...]
    ) -> EntailmentResult: ...


class NliClassifierPort(Protocol):
    def classify(self, premise: str, hypothesis: str) -> NliClassification: ...

    def close(self) -> None: ...


MAX_PREMISE_WINDOW_DEPTH = 4


class WindowedClaimVerifier:
    """Verify a summary point against its cited evidence with the pinned NLI model.

    Summary points cite whole chunks, which can exceed the model's input
    length together with the point. The classifier never truncates; when a
    premise overflows, it is split into two overlapping word windows and each is
    checked recursively (bounded depth). A point is entailed only if some window
    of a cited chunk entails it; a point that overflows on its own fails closed.
    """

    def __init__(self, classifier: NliClassifierPort) -> None:
        if not callable(getattr(classifier, "classify", None)) or not callable(
            getattr(classifier, "close", None)
        ):
            raise ValueError("classifier must implement classify and close")
        self._classifier = classifier

    async def verify(
        self, claim: RagClaim, *, evidence: tuple[RetrievedEvidence, ...]
    ) -> EntailmentResult:
        if not isinstance(claim, RagClaim) or not isinstance(evidence, tuple):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        by_id = {item.evidence_id: item for item in evidence}
        if len(by_id) != len(evidence) or not claim.evidence_ids:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if any(evidence_id not in by_id for evidence_id in claim.evidence_ids):
            raise RagOperationalError(RagErrorCode.INVALID_EVIDENCE_REFERENCE)
        for evidence_id in dict.fromkeys(claim.evidence_ids):
            if self._entailed(by_id[evidence_id].exact_text, claim.text, depth=0):
                return EntailmentResult.ENTAILED
        return EntailmentResult.NOT_ENTAILED

    def _entailed(self, premise: str, hypothesis: str, *, depth: int) -> bool:
        try:
            result = self._classifier.classify(premise, hypothesis)
        except NliInputOverflowError:
            words = premise.split()
            if depth >= MAX_PREMISE_WINDOW_DEPTH or len(words) < 2:
                raise RagOperationalError(RagErrorCode.ENTAILMENT_INPUT_OVERFLOW) from None
            half = len(words) // 2
            overlap = max(1, len(words) // 8)
            left = " ".join(words[: half + overlap])
            right = " ".join(words[max(0, half - overlap) :])
            return self._entailed(left, hypothesis, depth=depth + 1) or self._entailed(
                right, hypothesis, depth=depth + 1
            )
        except NliModelUnavailableError:
            raise RagOperationalError(RagErrorCode.NLI_MODEL_UNAVAILABLE) from None
        except NliInferenceError:
            raise RagOperationalError(RagErrorCode.NLI_INFERENCE_FAILED) from None
        if not isinstance(result, NliClassification):
            raise RagOperationalError(RagErrorCode.NLI_INFERENCE_FAILED)
        return result.decision is NliDecision.ENTAILED

    def close(self) -> None:
        self._classifier.close()


class SummaryOutcome(StrEnum):
    READY = "READY"
    NOT_PREPARED = "NOT_PREPARED"
    NO_VERIFIED_POINTS = "NO_VERIFIED_POINTS"


@dataclass(frozen=True, slots=True)
class SummaryResult:
    outcome: SummaryOutcome
    response: RenderedRagResponse | None
    document_version_ids: tuple[int, ...]
    missing_document_version_ids: tuple[int, ...] = ()
    dropped_points: int = 0
    from_cache: bool = False
    skipped_sections: int = 0


# Bad model output for one section; anything else (model or index outages)
# still fails the whole document.
_SKIPPABLE_SECTION_ERRORS = frozenset(
    {
        RagErrorCode.INVALID_STRUCTURED_OUTPUT,
        RagErrorCode.INVALID_EVIDENCE_REFERENCE,
        RagErrorCode.UNCITED_CLAIM,
        RagErrorCode.ENTAILMENT_INPUT_OVERFLOW,
    }
)


class _NotPrepared(Exception):
    """A summary could not be computed within the request budget."""


class DocumentSummarizer:
    def __init__(
        self,
        repository: SummaryRepositoryPort,
        generator: SummaryGeneratorPort,
        claim_verifier: ClaimVerifierPort,
        citation_validator: CitationValidator,
        *,
        model_identifier: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(model_identifier, str) or not model_identifier.strip():
            raise ValueError("model_identifier must be non-blank")
        self._repository = repository
        self._generator = generator
        self._verifier = claim_verifier
        self._validator = citation_validator
        self._model_identifier = model_identifier
        self._clock = clock

    def published_document_versions(self) -> tuple[int, ...]:
        """Return the document versions currently published in the active index."""
        return tuple(self._repository.pin_query_snapshot(GlobalScope()).document_version_ids)

    async def summarize_document(
        self, document_version_id: int, language: str, *, deadline: float | None = None
    ) -> SummaryResult:
        return await self.summarize_course((document_version_id,), language, deadline=deadline)

    async def summarize_course(
        self,
        document_version_ids: Sequence[int],
        language: str,
        *,
        deadline: float | None = None,
    ) -> SummaryResult:
        """Summarize one or more document versions as one cited answer."""
        versions = tuple(dict.fromkeys(document_version_ids))
        if not versions or len(versions) > MAX_COURSE_DOCUMENTS:
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        if language not in SUMMARY_LANGUAGES:
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        snapshot = self._repository.pin_query_snapshot(HistoricalScope(versions))
        summaries: list[StoredDocumentSummary] = []
        missing: list[int] = []
        from_cache = True
        dropped = 0
        skipped = 0
        for version in versions:
            key = self._key(snapshot, version, language)
            cached = self._repository.get_document_summary(key)
            if cached is not None:
                summaries.append(cached)
                dropped += cached.dropped_points
                continue
            from_cache = False
            try:
                computed, computed_dropped, computed_skipped = await self._compute(
                    snapshot, key, deadline
                )
            except _NotPrepared:
                missing.append(version)
                continue
            dropped += computed_dropped
            skipped += computed_skipped
            if computed is not None:
                self._repository.put_document_summary(computed)
                summaries.append(computed)
        if missing:
            return SummaryResult(
                SummaryOutcome.NOT_PREPARED,
                None,
                versions,
                missing_document_version_ids=tuple(missing),
                dropped_points=dropped,
                skipped_sections=skipped,
            )
        if not summaries:
            return SummaryResult(
                SummaryOutcome.NO_VERIFIED_POINTS,
                None,
                versions,
                dropped_points=dropped,
                skipped_sections=skipped,
            )
        response = self._render(snapshot, versions, language, summaries)
        return SummaryResult(
            SummaryOutcome.READY,
            response,
            versions,
            dropped_points=dropped,
            from_cache=from_cache,
            skipped_sections=skipped,
        )

    def _key(self, snapshot: QuerySnapshot, version: int, language: str) -> SummaryCacheKey:
        return SummaryCacheKey(
            document_version_id=version,
            generation_id=snapshot.generation.generation_id,
            content_revision=snapshot.generation.content_revision,
            policy_version=SUMMARY_POLICY_VERSION,
            model_identifier=self._model_identifier,
            language=language,
        )

    async def _compute(
        self, snapshot: QuerySnapshot, key: SummaryCacheKey, deadline: float | None
    ) -> tuple[StoredDocumentSummary | None, int, int]:
        chunks = self._repository.list_chunks(snapshot.generation, key.document_version_id)
        sections = _sections(chunks)
        if not sections:
            return None, 0, 0
        if deadline is not None and (
            deadline - self._clock() < SECTION_TIME_RESERVE_SECONDS * len(sections)
        ):
            raise _NotPrepared()
        section_points: list[list[SummaryPoint]] = []
        dropped = 0
        skipped = 0
        for section in sections:
            outcome = await self._summarize_section_or_skip(snapshot, key, section, deadline)
            if outcome is None:
                skipped += 1
                continue
            points, section_dropped = outcome
            section_points.append(points)
            dropped += section_dropped
        points = _spread(section_points, MAX_SUMMARY_POINTS)
        if not points:
            return None, dropped, skipped
        return (
            StoredDocumentSummary(
                key=key,
                points=tuple(points),
                sections_total=len(sections),
                dropped_points=dropped,
            ),
            dropped,
            skipped,
        )

    async def _summarize_section_or_skip(
        self,
        snapshot: QuerySnapshot,
        key: SummaryCacheKey,
        section: tuple[ChunkRecord, ...],
        deadline: float | None,
    ) -> tuple[list[SummaryPoint], int] | None:
        """Summarize one section, retrying bad model output once, else skip it.

        One malformed or ungrounded section output (for example a citation of
        evidence the model was not given) must not discard every other
        section. Infrastructure failures still fail the whole document.
        """
        try:
            return await self._summarize_section(snapshot, key, section)
        except RagOperationalError as error:
            if error.code not in _SKIPPABLE_SECTION_ERRORS:
                raise
        if deadline is not None and deadline - self._clock() < SECTION_TIME_RESERVE_SECONDS:
            return None
        try:
            return await self._summarize_section(snapshot, key, section)
        except RagOperationalError as error:
            if error.code not in _SKIPPABLE_SECTION_ERRORS:
                raise
        return None

    async def _summarize_section(
        self,
        snapshot: QuerySnapshot,
        key: SummaryCacheKey,
        section: tuple[ChunkRecord, ...],
    ) -> tuple[list[SummaryPoint], int]:
        authorities = self._repository.materialize_snapshot_chunks(
            snapshot, tuple(chunk.chunk_key for chunk in section)
        )
        evidence = tuple(
            evidence_from_authoritative_chunk(
                snapshot,
                item,
                evidence_id=f"e{position}",
                dense_rank=position,
                lexical_rank=None,
                fused_rank=position,
            )
            for position, item in enumerate(authorities, 1)
        )
        request = _request(snapshot, (key.document_version_id,), key.language, evidence)
        output = await self._generator.generate(
            request,
            SufficiencyDecision(
                status=SufficiencyStatus.SUPPORTED,
                evidence_count=len(evidence),
                token_count=sum(item.token_count for item in evidence),
                policy_version=SUMMARY_POLICY_VERSION,
            ),
        )
        if output.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            return [], 0
        by_id = {item.evidence_id: item for item in evidence}
        points: list[SummaryPoint] = []
        dropped = 0
        for claim in output.claims:
            if any(evidence_id not in by_id for evidence_id in claim.evidence_ids):
                raise RagOperationalError(RagErrorCode.INVALID_EVIDENCE_REFERENCE)
            result = await self._verifier.verify(claim, evidence=evidence)
            if result is not EntailmentResult.ENTAILED:
                dropped += 1
                continue
            points.append(
                SummaryPoint(
                    claim.text,
                    tuple(dict.fromkeys(by_id[eid].chunk_key for eid in claim.evidence_ids))[
                        :MAX_SUMMARY_CHUNK_KEYS_PER_POINT
                    ],
                )
            )
        return points, dropped

    def _render(
        self,
        snapshot: QuerySnapshot,
        versions: tuple[int, ...],
        language: str,
        summaries: list[StoredDocumentSummary],
    ) -> RenderedRagResponse:
        points = _spread([list(summary.points) for summary in summaries], MAX_SUMMARY_POINTS)
        chunk_keys = tuple(dict.fromkeys(key for point in points for key in point.chunk_keys))
        authorities = self._repository.materialize_snapshot_chunks(snapshot, chunk_keys)
        evidence = tuple(
            evidence_from_authoritative_chunk(
                snapshot,
                item,
                evidence_id=f"e{position}",
                dense_rank=position,
                lexical_rank=None,
                fused_rank=position,
            )
            for position, item in enumerate(authorities, 1)
        )
        id_by_key = {item.chunk_key: item.evidence_id for item in evidence}
        if set(id_by_key) != set(chunk_keys):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        request = _request(snapshot, versions, language, evidence, summary=True)
        output = GroundedModelOutput(
            status=RagAnswerStatus.ANSWER,
            answer_text="\n".join(point.text for point in points),
            language=language,
            claims=tuple(
                RagClaim(
                    f"p{position}", point.text, tuple(id_by_key[key] for key in point.chunk_keys)
                )
                for position, point in enumerate(points, 1)
            ),
            generation_id=snapshot.generation.generation_id,
            content_revision=snapshot.generation.content_revision,
            policy_version=SUMMARY_POLICY_VERSION,
        )
        return render_rag_answer(self._validator.validate(output, request))


def _request(
    snapshot: QuerySnapshot,
    versions: tuple[int, ...],
    language: str,
    evidence: tuple[RetrievedEvidence, ...],
    *,
    summary: bool = False,
) -> GroundedAnswerRequest:
    question = (_SUMMARY_QUESTIONS if summary else _SECTION_QUESTIONS)[language]
    return GroundedAnswerRequest.from_query(
        RagQuery(question=question, scope=HistoricalScope(versions), answer_language=language),
        generation_id=snapshot.generation.generation_id,
        content_revision=snapshot.generation.content_revision,
        evidence=evidence,
        policy_version=SUMMARY_POLICY_VERSION,
    )


def _sections(chunks: tuple[ChunkRecord, ...]) -> list[tuple[ChunkRecord, ...]]:
    """Group consecutive chunks in reading order into bounded sections."""
    sections: list[tuple[ChunkRecord, ...]] = []
    current: list[ChunkRecord] = []
    tokens = 0
    for chunk in sorted(chunks, key=lambda item: item.ordinal):
        if current and (
            tokens + chunk.token_count > MAX_SECTION_TOKENS or len(current) >= MAX_SECTION_CHUNKS
        ):
            sections.append(tuple(current))
            current, tokens = [], 0
        current.append(chunk)
        tokens += chunk.token_count
    if current:
        sections.append(tuple(current))
    return sections


def _spread(groups: list[list[SummaryPoint]], limit: int) -> list[SummaryPoint]:
    """Take points evenly across groups in order, up to ``limit`` in total."""
    non_empty = [group for group in groups if group]
    if not non_empty:
        return []
    quota = math.ceil(limit / len(non_empty))
    selected: list[SummaryPoint] = []
    for group in non_empty:
        selected.extend(group[:quota])
    return selected[:limit]
