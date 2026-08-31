"""Guarded application-facing composition for the Phase 4 local RAG path."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    GroundedAnswerRequest,
    GroundedModelOutput,
    RagAnswer,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
    RenderedRagResponse,
    RetrievedEvidence,
)
from .generation import CitationValidator, GroundedAnswerGenerator, render_rag_answer
from .retrieval import (
    EvidenceSelector,
    EvidenceSufficiencyGate,
    FusedCandidate,
    HybridRetriever,
    QuerySnapshot,
    RetrievalCandidateSet,
    SufficiencyDecision,
    reciprocal_rank_fusion,
)

_DEFAULT_RRF_K = 60
_DEFAULT_GENERATION_POLICY_VERSION = "grounded-v1"


class _Retriever(Protocol):
    def retrieve_candidates(self, query: RagQuery) -> RetrievalCandidateSet: ...


class _Selector(Protocol):
    def select(
        self, snapshot: QuerySnapshot, candidates: tuple[FusedCandidate, ...]
    ) -> tuple[RetrievedEvidence, ...]: ...


class _SufficiencyGate(Protocol):
    def evaluate(
        self, evidence: tuple[RetrievedEvidence, ...], *, snapshot: QuerySnapshot
    ) -> SufficiencyDecision: ...


class _Generator(Protocol):
    async def generate(
        self,
        request: GroundedAnswerRequest,
        sufficiency: SufficiencyDecision,
    ) -> GroundedModelOutput: ...


class _Validator(Protocol):
    def validate(
        self, output: GroundedModelOutput, request: GroundedAnswerRequest
    ) -> RagAnswer: ...


class LocalRagService:
    """Compose one immutable query through retrieval, grounding, and rendering.

    The service owns no backend resources.  The retriever owns the verified
    embedding-release boundary; all other collaborators are caller-owned and
    are never closed or reconfigured here.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever | _Retriever,
        evidence_selector: EvidenceSelector | _Selector,
        sufficiency_gate: EvidenceSufficiencyGate | _SufficiencyGate,
        answer_generator: GroundedAnswerGenerator | _Generator,
        citation_validator: CitationValidator | _Validator | None = None,
        rrf_k: int = _DEFAULT_RRF_K,
        generation_policy_version: str = _DEFAULT_GENERATION_POLICY_VERSION,
    ) -> None:
        for dependency, name in (
            (retriever, "retriever"),
            (evidence_selector, "evidence_selector"),
            (sufficiency_gate, "sufficiency_gate"),
            (answer_generator, "answer_generator"),
        ):
            if dependency is None:
                raise ValueError(f"{name} is required")
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if (
            not isinstance(generation_policy_version, str)
            or not generation_policy_version.strip()
            or len(generation_policy_version) > MAX_RAG_TEXT_CHARACTERS
        ):
            raise ValueError("generation_policy_version must be non-blank and bounded")
        self._retriever = retriever
        self._evidence_selector = evidence_selector
        self._sufficiency_gate = sufficiency_gate
        self._answer_generator = answer_generator
        self._citation_validator = citation_validator or CitationValidator()
        self._rrf_k = rrf_k
        self._generation_policy_version = generation_policy_version.strip()
        _require_callable(self._retriever, "retrieve_candidates", "retriever")
        _require_callable(self._evidence_selector, "select", "evidence_selector")
        _require_callable(self._sufficiency_gate, "evaluate", "sufficiency_gate")
        _require_callable(self._answer_generator, "generate", "answer_generator")
        _require_callable(self._citation_validator, "validate", "citation_validator")

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        """Run the mandatory snapshot → evidence → answer path exactly once."""
        if not isinstance(query, RagQuery):
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)

        candidate_set = self._retrieve(query)
        fused_candidates = self._fuse(candidate_set)
        evidence = self._select(candidate_set, fused_candidates)
        sufficiency = self._evaluate(evidence, candidate_set)
        request = self._build_request(query, candidate_set, evidence)
        output = await self._generate(request, sufficiency)
        answer = self._validate(output, request)
        return _render(answer)

    def _retrieve(self, query: RagQuery) -> RetrievalCandidateSet:
        try:
            candidate_set = self._retriever.retrieve_candidates(query)
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        if not isinstance(candidate_set, RetrievalCandidateSet):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if candidate_set.embedding_release_verified is not True:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return candidate_set

    def _fuse(self, candidate_set: RetrievalCandidateSet) -> tuple[FusedCandidate, ...]:
        try:
            return reciprocal_rank_fusion(
                candidate_set.dense_hits,
                candidate_set.lexical_result.hits,
                rrf_k=self._rrf_k,
            )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None

    def _select(
        self,
        candidate_set: RetrievalCandidateSet,
        fused_candidates: tuple[FusedCandidate, ...],
    ) -> tuple[RetrievedEvidence, ...]:
        try:
            evidence = self._evidence_selector.select(
                candidate_set.snapshot,
                fused_candidates,
            )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        if not isinstance(evidence, tuple):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return evidence

    def _evaluate(
        self,
        evidence: tuple[RetrievedEvidence, ...],
        candidate_set: RetrievalCandidateSet,
    ) -> SufficiencyDecision:
        try:
            decision = self._sufficiency_gate.evaluate(
                evidence,
                snapshot=candidate_set.snapshot,
            )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        if not isinstance(decision, SufficiencyDecision):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return decision

    def _build_request(
        self,
        query: RagQuery,
        candidate_set: RetrievalCandidateSet,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GroundedAnswerRequest:
        try:
            request_evidence = tuple(evidence)
            return GroundedAnswerRequest.from_query(
                query,
                generation_id=candidate_set.snapshot.generation.generation_id,
                content_revision=candidate_set.snapshot.generation.content_revision,
                evidence=request_evidence,
                policy_version=self._generation_policy_version,
            )
        except RagOperationalError:
            raise
        except (TypeError, ValueError, AttributeError):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None

    async def _generate(
        self,
        request: GroundedAnswerRequest,
        sufficiency: SufficiencyDecision,
    ) -> GroundedModelOutput:
        try:
            output = await self._answer_generator.generate(request, sufficiency)
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE) from None
        if not isinstance(output, GroundedModelOutput):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        return output

    def _validate(
        self,
        output: GroundedModelOutput,
        request: GroundedAnswerRequest,
    ) -> RagAnswer:
        try:
            answer = self._citation_validator.validate(output, request)
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT) from None
        if not isinstance(answer, RagAnswer):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        return answer


def _render(answer: RagAnswer) -> RenderedRagResponse:
    try:
        return render_rag_answer(answer)
    except RagOperationalError:
        raise
    except Exception:
        raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT) from None


def _require_callable(component: object, attribute: str, name: str) -> None:
    if not callable(getattr(component, attribute, None)):
        raise ValueError(f"{name} must provide {attribute}")


GuardedLocalRagService = LocalRagService

__all__ = ["GuardedLocalRagService", "LocalRagService"]
