"""Guarded application-facing composition for the Phase 4 local RAG path."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    AtomicRequirementDecomposition,
    EntailmentResult,
    EvidenceSpan,
    GroundedAnswerRequest,
    GroundedModelOutput,
    RagAnswer,
    RagAnswerStatus,
    RagClaim,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
    RenderedRagResponse,
    Requirement,
    RequirementCoverageDecision,
    RequirementCoverageJudgment,
    RequirementEvidenceSpans,
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
    RequirementCoverageClassifier,
    RequirementDecomposer,
    _validate_span_candidates,
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


class _AtomicRequirementDecomposer(Protocol):
    async def decompose(self, query: RagQuery) -> AtomicRequirementDecomposition: ...


class _EvidenceSpanExtractor(Protocol):
    async def extract(
        self, requirements: RequirementSet, *, evidence: tuple[RetrievedEvidence, ...]
    ) -> tuple[RequirementEvidenceSpans, ...]: ...


class _EntailmentVerifier(Protocol):
    async def verify(
        self, requirement: Requirement, spans: RequirementEvidenceSpans
    ) -> EntailmentResult: ...


class _ClaimEntailmentVerifier(Protocol):
    async def verify(
        self,
        claim: RagClaim,
        *,
        evidence: tuple[RetrievedEvidence, ...],
        validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
    ) -> EntailmentResult: ...


class _Validator(Protocol):
    def validate(
        self, output: GroundedModelOutput, request: GroundedAnswerRequest
    ) -> RagAnswer: ...


def _validate_atomic_evidence(
    snapshot: QuerySnapshot,
    evidence: tuple[RetrievedEvidence, ...],
) -> None:
    """Reassert the selector's authoritative snapshot boundary before model use."""
    if not isinstance(snapshot, QuerySnapshot):
        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    authorized_versions = frozenset(snapshot.document_version_ids)
    for item in evidence:
        if (
            item.document_version_id not in authorized_versions
            or item.generation_id != snapshot.generation.generation_id
            or item.content_revision != snapshot.generation.content_revision
            or not item.source_spans
        ):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)


def _validate_atomic_spans(
    requirements: RequirementSet,
    spans: tuple[RequirementEvidenceSpans, ...],
    evidence: tuple[RetrievedEvidence, ...],
) -> tuple[RequirementEvidenceSpans, ...]:
    """Validate extractor output before it can influence entailment or generation."""
    if not isinstance(spans, tuple) or len(spans) != len(requirements.requirements):
        raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
    expected = tuple(item.requirement_id for item in requirements.requirements)
    by_requirement: dict[str, RequirementEvidenceSpans] = {}
    for requirement_spans in spans:
        if (
            not isinstance(requirement_spans, RequirementEvidenceSpans)
            or requirement_spans.requirement_id not in expected
            or requirement_spans.requirement_id in by_requirement
        ):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        if not all(isinstance(span, EvidenceSpan) for span in requirement_spans.spans):
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
        validated_spans, _stats = _validate_span_candidates(requirement_spans.spans, evidence)
        if requirement_spans.spans and not validated_spans:
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
        by_requirement[requirement_spans.requirement_id] = RequirementEvidenceSpans(
            requirement_spans.requirement_id, validated_spans
        )
    if set(by_requirement) != set(expected):
        raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
    return tuple(by_requirement[requirement_id] for requirement_id in expected)


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
        atomic_requirement_decomposer: _AtomicRequirementDecomposer | None = None,
        evidence_span_extractor: _EvidenceSpanExtractor | None = None,
        entailment_verifier: _EntailmentVerifier | None = None,
        claim_entailment_verifier: _ClaimEntailmentVerifier | None = None,
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
        self._claim_entailment_verifier = claim_entailment_verifier
        self._rrf_k = rrf_k
        self._generation_policy_version = generation_policy_version.strip()
        _require_callable(self._retriever, "retrieve_candidates", "retriever")
        _require_callable(self._evidence_selector, "select", "evidence_selector")
        _require_callable(self._sufficiency_gate, "evaluate", "sufficiency_gate")
        _require_callable(self._answer_generator, "generate", "answer_generator")
        _require_callable(self._citation_validator, "validate", "citation_validator")
        if self._claim_entailment_verifier is not None:
            _require_callable(
                self._claim_entailment_verifier,
                "verify",
                "claim_entailment_verifier",
            )
        if self._semantic_classifier is not None:
            _require_callable(self._semantic_classifier, "classify", "semantic_classifier")
        if self._requirement_decomposer is not None:
            if self._requirement_coverage_classifier is None:
                raise ValueError("requirement_decomposer requires requirement_coverage_classifier")
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
        elif (
            self._requirement_coverage_classifier is not None
            and self._atomic_requirement_decomposer is None
        ):
            raise ValueError(
                "requirement_coverage_classifier requires a requirement decomposer "
                "or atomic requirement decomposer"
            )

        atomic_structure = (
            self._atomic_requirement_decomposer,
            self._evidence_span_extractor,
        )
        if any(component is not None for component in atomic_structure):
            if (
                any(component is None for component in atomic_structure)
                or self._semantic_classifier is not None
                or self._requirement_decomposer is not None
            ):
                raise ValueError(
                    "atomic semantic components must be provided together and cannot be combined"
                )
            if self._requirement_coverage_classifier is None and self._entailment_verifier is None:
                raise ValueError(
                    "atomic semantic path requires requirement coverage or an entailment verifier"
                )
            if (
                self._requirement_coverage_classifier is not None
                and self._entailment_verifier is not None
            ):
                raise ValueError(
                    "atomic semantic path cannot combine requirement coverage "
                    "with requirement-text entailment"
                )

            _require_callable(
                self._atomic_requirement_decomposer,
                "decompose",
                "atomic_requirement_decomposer",
            )
            _require_callable(
                self._evidence_span_extractor,
                "extract",
                "evidence_span_extractor",
            )
            if self._requirement_coverage_classifier is not None:
                _require_callable(
                    self._requirement_coverage_classifier,
                    "classify",
                    "requirement_coverage_classifier",
                )
            if self._entailment_verifier is not None:
                _require_callable(
                    self._entailment_verifier,
                    "verify",
                    "entailment_verifier",
                )
        elif self._entailment_verifier is not None:
            raise ValueError("entailment_verifier requires atomic semantic components")

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
        atomic_spans = atomic_decision[1] if atomic_decision is not None else ()
        atomic_result = atomic_decision[0] if atomic_decision is not None else None
        requirement_decision = (
            atomic_result if isinstance(atomic_result, RequirementCoverageDecision) else None
        )
        semantic_decision = (
            atomic_result if isinstance(atomic_result, SemanticCoverageDecision) else None
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
                validated_requirement_spans=atomic_spans,
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
        output = await self._verify_generated_claims(output, evidence, request)
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
        self,
        query: RagQuery,
        request: GroundedAnswerRequest,
        evidence: tuple[RetrievedEvidence, ...],
        candidate_set: RetrievalCandidateSet,
        sufficiency: SufficiencyDecision,
    ) -> (
        tuple[
            RequirementCoverageDecision | SemanticCoverageDecision | None,
            tuple[RequirementEvidenceSpans, ...],
        ]
        | None
    ):
        if (
            sufficiency.status is SufficiencyStatus.UNSUPPORTED
            or self._atomic_requirement_decomposer is None
        ):
            return None

        assert self._evidence_span_extractor is not None

        try:
            _validate_atomic_evidence(candidate_set.snapshot, evidence)

            decomposition = await self._atomic_requirement_decomposer.decompose(query)
            if decomposition.status.value == "TOO_MANY_REQUIREMENTS":
                return (
                    SemanticCoverageDecision(
                        SemanticCoverageStatus.UNSUPPORTED,
                        (),
                        request.generation_id,
                        request.content_revision,
                        request.policy_version,
                    ),
                    (),
                )

            requirements = RequirementSet(decomposition.requirements)

            spans = await self._evidence_span_extractor.extract(
                requirements,
                evidence=evidence,
            )
            spans = _validate_atomic_spans(requirements, spans, evidence)

            if self._requirement_coverage_classifier is not None:
                decision = await self._requirement_coverage_classifier.classify(
                    requirements,
                    question=request.question,
                    answer_language=request.answer_language,
                    generation_id=candidate_set.snapshot.generation.generation_id,
                    content_revision=candidate_set.snapshot.generation.content_revision,
                    policy_version=request.policy_version,
                    evidence=evidence,
                )
                if not isinstance(decision, RequirementCoverageDecision):
                    raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
                return decision, spans

            assert self._entailment_verifier is not None

            judgments: list[RequirementCoverageJudgment] = []
            for requirement, requirement_spans in zip(
                requirements.requirements,
                spans,
                strict=True,
            ):
                result = await self._entailment_verifier.verify(
                    requirement,
                    requirement_spans,
                )
                evidence_ids = tuple(
                    dict.fromkeys(span.evidence_id for span in requirement_spans.spans)
                )
                support = (
                    RequirementSupport.SUPPORTED
                    if result.value == "ENTAILED"
                    else RequirementSupport.UNSUPPORTED
                )
                judgments.append(
                    RequirementCoverageJudgment(
                        requirement.requirement_id,
                        support,
                        (evidence_ids if support is RequirementSupport.SUPPORTED else ()),
                    )
                )

            status = (
                SemanticCoverageStatus.UNSUPPORTED
                if not any(
                    judgment.support is RequirementSupport.SUPPORTED for judgment in judgments
                )
                else SemanticCoverageStatus.SUPPORTED
                if all(judgment.support is RequirementSupport.SUPPORTED for judgment in judgments)
                else SemanticCoverageStatus.PARTIAL
            )

            return (
                RequirementCoverageDecision(
                    requirements,
                    tuple(judgments),
                    status,
                    request.generation_id,
                    request.content_revision,
                    request.policy_version,
                ),
                spans,
            )
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
        validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
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
                validated_requirement_spans=validated_requirement_spans,
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

    async def _verify_generated_claims(
        self,
        output: GroundedModelOutput,
        evidence: tuple[RetrievedEvidence, ...],
        request: GroundedAnswerRequest,
    ) -> GroundedModelOutput:
        verifier = self._claim_entailment_verifier
        if verifier is None:
            return output

        surviving_claims: list[RagClaim] = []

        for claim in output.claims:
            if not isinstance(claim, RagClaim):
                raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
            try:
                if request.validated_requirement_spans:
                    result = await verifier.verify(
                        claim,
                        evidence=evidence,
                        validated_requirement_spans=(request.validated_requirement_spans),
                    )
                else:
                    result = await verifier.verify(
                        claim,
                        evidence=evidence,
                    )
            except RagOperationalError:
                raise
            except Exception:
                raise RagOperationalError(RagErrorCode.NLI_INFERENCE_FAILED) from None
            if not isinstance(result, EntailmentResult):
                raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT)
            if result is EntailmentResult.ENTAILED:
                surviving_claims.append(claim)

        if len(surviving_claims) == len(output.claims):
            return output

        if not surviving_claims:
            return build_insufficient_model_output(request)

        return GroundedModelOutput(
            status=RagAnswerStatus.PARTIAL_ANSWER,
            answer_text=output.answer_text,
            language=output.language,
            claims=tuple(surviving_claims),
            generation_id=output.generation_id,
            content_revision=output.content_revision,
            policy_version=output.policy_version,
        )

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
