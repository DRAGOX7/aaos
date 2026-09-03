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
    RequirementCoverageDecision,
    RequirementCoverageJudgment,
    RequirementSet,
    RequirementSupport,
    RetrievedEvidence,
    SemanticCoverageDecision,
    SemanticCoverageStatus,
)
from .generation import (
    CitationValidator,
    GroundedAnswerGenerator,
    build_insufficient_model_output,
    render_rag_answer,
)
from .retrieval import (
    EvidenceSelector,
    EvidenceSufficiencyGate,
    FusedCandidate,
    HybridRetriever,
    QuerySnapshot,
    RetrievalCandidateSet,
    SufficiencyDecision,
    SufficiencyStatus,
    reciprocal_rank_fusion,
)
from .semantic_coverage import (
    AtomicRequirementDecomposer,
    EvidenceSpanExtractor,
    IndependentEntailmentVerifier,
    RequirementCoverageClassifier,
    RequirementDecomposer,
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


class _SemanticClassifier(Protocol):
    async def classify(self, request: GroundedAnswerRequest) -> SemanticCoverageDecision: ...


class _RequirementDecomposer(Protocol):
    async def decompose(self, query: RagQuery) -> RequirementSet: ...


class _RequirementCoverageClassifier(Protocol):
    async def classify(
        self,
        requirements: RequirementSet,
        *,
        question: str,
        answer_language: str,
        generation_id: int,
        content_revision: int,
        policy_version: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> RequirementCoverageDecision: ...


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
        semantic_classifier: _SemanticClassifier | None = None,
        requirement_decomposer: _RequirementDecomposer | RequirementDecomposer | None = None,
        requirement_coverage_classifier: (
            _RequirementCoverageClassifier | RequirementCoverageClassifier | None
        ) = None,
        atomic_requirement_decomposer: AtomicRequirementDecomposer | None = None,
        evidence_span_extractor: EvidenceSpanExtractor | None = None,
        entailment_verifier: IndependentEntailmentVerifier | None = None,
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
        self._semantic_classifier = semantic_classifier
        self._requirement_decomposer = requirement_decomposer
        self._requirement_coverage_classifier = requirement_coverage_classifier
        self._atomic_requirement_decomposer = atomic_requirement_decomposer
        self._evidence_span_extractor = evidence_span_extractor
        self._entailment_verifier = entailment_verifier
        self._rrf_k = rrf_k
        self._generation_policy_version = generation_policy_version.strip()
        _require_callable(self._retriever, "retrieve_candidates", "retriever")
        _require_callable(self._evidence_selector, "select", "evidence_selector")
        _require_callable(self._sufficiency_gate, "evaluate", "sufficiency_gate")
        _require_callable(self._answer_generator, "generate", "answer_generator")
        _require_callable(self._citation_validator, "validate", "citation_validator")
        if self._semantic_classifier is not None:
            _require_callable(self._semantic_classifier, "classify", "semantic_classifier")
        if (self._requirement_decomposer is None) != (
            self._requirement_coverage_classifier is None
        ):
            raise ValueError(
                "requirement_decomposer and requirement_coverage_classifier must be provided together"
            )
        if self._requirement_decomposer is not None:
            if self._semantic_classifier is not None:
                raise ValueError(
                    "semantic_classifier cannot be combined with requirement classifiers"
                )
            _require_callable(self._requirement_decomposer, "decompose", "requirement_decomposer")
            _require_callable(
                self._requirement_coverage_classifier,
                "classify",
                "requirement_coverage_classifier",
            )
        atomic_components = (
            self._atomic_requirement_decomposer,
            self._evidence_span_extractor,
            self._entailment_verifier,
        )
        if any(component is not None for component in atomic_components):
            if any(component is None for component in atomic_components) or self._semantic_classifier is not None or self._requirement_decomposer is not None:
                raise ValueError("atomic semantic components must be provided together and cannot be combined")
            _require_callable(self._atomic_requirement_decomposer, "decompose", "atomic_requirement_decomposer")
            _require_callable(self._evidence_span_extractor, "extract", "evidence_span_extractor")
            _require_callable(self._entailment_verifier, "verify", "entailment_verifier")

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        """Run the mandatory snapshot → evidence → answer path exactly once."""
        if not isinstance(query, RagQuery):
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)

        candidate_set = self._retrieve(query)
        fused_candidates = self._fuse(candidate_set)
        evidence = self._select(candidate_set, fused_candidates)
        sufficiency = self._evaluate(evidence, candidate_set)
        request = self._build_request(query, candidate_set, evidence)
        atomic_decision = await self._classify_atomic_requirements(
            query, request, evidence, candidate_set, sufficiency
        )
        requirement_decision = (
            atomic_decision if isinstance(atomic_decision, RequirementCoverageDecision) else None
        )
        semantic_decision = (
            atomic_decision if isinstance(atomic_decision, SemanticCoverageDecision) else None
        )
        if requirement_decision is None and semantic_decision is None:
            requirement_decision = await self._classify_requirements(
            query, request, evidence, candidate_set, sufficiency
        )
        if requirement_decision is None and semantic_decision is None:
            semantic_decision = await self._classify(request, sufficiency)
        if requirement_decision is not None:
            request = self._build_request(
                query,
                candidate_set,
                evidence,
                requirement_coverage=requirement_decision,
            )
        elif semantic_decision is not None:
            request = self._build_request(query, candidate_set, evidence, semantic_decision)
        if (
            sufficiency.status is SufficiencyStatus.UNSUPPORTED
            or (
                requirement_decision is not None
                and requirement_decision.status is SemanticCoverageStatus.UNSUPPORTED
            )
            or (
                semantic_decision is not None
                and semantic_decision.status is SemanticCoverageStatus.UNSUPPORTED
            )
        ):
            output = build_insufficient_model_output(request)
        else:
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

    async def _classify_atomic_requirements(
        self, query: RagQuery, request: GroundedAnswerRequest, evidence: tuple[RetrievedEvidence, ...],
        candidate_set: RetrievalCandidateSet, sufficiency: SufficiencyDecision,
    ) -> RequirementCoverageDecision | SemanticCoverageDecision | None:
        if sufficiency.status is SufficiencyStatus.UNSUPPORTED or self._atomic_requirement_decomposer is None:
            return None
        assert self._evidence_span_extractor is not None
        assert self._entailment_verifier is not None
        try:
            decomposition = await self._atomic_requirement_decomposer.decompose(query)
            if decomposition.status.value == "TOO_MANY_REQUIREMENTS":
                return SemanticCoverageDecision(
                    SemanticCoverageStatus.UNSUPPORTED,
                    (),
                    request.generation_id,
                    request.content_revision,
                    request.policy_version,
                )
            requirements = RequirementSet(decomposition.requirements)
            spans = await self._evidence_span_extractor.extract(requirements, evidence=evidence)
            judgments: list[RequirementCoverageJudgment] = []
            for requirement, requirement_spans in zip(requirements.requirements, spans, strict=True):
                result = await self._entailment_verifier.verify(requirement, requirement_spans)
                evidence_ids = tuple(dict.fromkeys(span.evidence_id for span in requirement_spans.spans))
                support = RequirementSupport.SUPPORTED if result.value == "ENTAILED" else RequirementSupport.UNSUPPORTED
                judgments.append(RequirementCoverageJudgment(requirement.requirement_id, support, evidence_ids if support is RequirementSupport.SUPPORTED else ()))
            status = SemanticCoverageStatus.UNSUPPORTED if not any(j.support is RequirementSupport.SUPPORTED for j in judgments) else SemanticCoverageStatus.SUPPORTED if all(j.support is RequirementSupport.SUPPORTED for j in judgments) else SemanticCoverageStatus.PARTIAL
            return RequirementCoverageDecision(requirements, tuple(judgments), status, request.generation_id, request.content_revision, request.policy_version)
        except RagOperationalError:
            raise
        except (AttributeError, TypeError, ValueError):
            raise RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE) from None

    def _build_request(
        self,
        query: RagQuery,
        candidate_set: RetrievalCandidateSet,
        evidence: tuple[RetrievedEvidence, ...],
        semantic_decision: SemanticCoverageDecision | None = None,
        requirement_coverage: RequirementCoverageDecision | None = None,
    ) -> GroundedAnswerRequest:
        try:
            request_evidence = tuple(evidence)
            return GroundedAnswerRequest.from_query(
                query,
                generation_id=candidate_set.snapshot.generation.generation_id,
                content_revision=candidate_set.snapshot.generation.content_revision,
                evidence=request_evidence,
                policy_version=self._generation_policy_version,
                semantic_decision=semantic_decision,
                requirement_coverage=requirement_coverage,
            )
        except RagOperationalError:
            raise
        except (TypeError, ValueError, AttributeError):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None

    async def _classify(
        self,
        request: GroundedAnswerRequest,
        sufficiency: SufficiencyDecision,
    ) -> SemanticCoverageDecision | None:
        if sufficiency.status is SufficiencyStatus.UNSUPPORTED:
            return None
        if self._semantic_classifier is None:
            raise RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE)
        try:
            decision = await self._semantic_classifier.classify(request)
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE) from None
        if not isinstance(decision, SemanticCoverageDecision):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        if (
            decision.generation_id != request.generation_id
            or decision.content_revision != request.content_revision
            or decision.policy_version != request.policy_version
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if decision.status is SemanticCoverageStatus.SUPPORTED and decision.unsupported_aspects:
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        return decision

    async def _classify_requirements(
        self,
        query: RagQuery,
        request: GroundedAnswerRequest,
        evidence: tuple[RetrievedEvidence, ...],
        candidate_set: RetrievalCandidateSet,
        sufficiency: SufficiencyDecision,
    ) -> RequirementCoverageDecision | None:
        if sufficiency.status is SufficiencyStatus.UNSUPPORTED:
            return None
        if self._requirement_decomposer is None or self._requirement_coverage_classifier is None:
            return None
        try:
            requirements = await self._requirement_decomposer.decompose(query)
            if not isinstance(requirements, RequirementSet):
                raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
            decision = await self._requirement_coverage_classifier.classify(
                requirements,
                question=request.question,
                answer_language=request.answer_language,
                generation_id=candidate_set.snapshot.generation.generation_id,
                content_revision=candidate_set.snapshot.generation.content_revision,
                policy_version=request.policy_version,
                evidence=evidence,
            )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE) from None
        if not isinstance(decision, RequirementCoverageDecision):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        if (
            decision.generation_id != request.generation_id
            or decision.content_revision != request.content_revision
            or decision.policy_version != request.policy_version
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return decision

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
