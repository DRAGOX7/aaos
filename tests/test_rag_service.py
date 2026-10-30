"""Task 19 guarded LocalRagService facade tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.rag import GuardedLocalRagService
from aaos.rag.contracts import (
    AtomicDecompositionStatus,
    AtomicRequirementDecomposition,
    Citation,
    DenseSearchHit,
    EntailmentResult,
    EvidenceSpan,
    GlobalScope,
    RagAnswer,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagClaim,
    RagErrorCode,
    RagGenerationIdentity,
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
from aaos.rag.generation import (
    CitationValidator,
    GroundedAnswerGenerator,
    GroundedAnswerRequest,
    GroundedModelOutput,
)
from aaos.rag.lexical import LexicalSearchHit, LexicalSearchResult
from aaos.rag.repository import QuerySnapshot
from aaos.rag.retrieval import (
    RetrievalCandidateSet,
    SufficiencyDecision,
    SufficiencyStatus,
)

GENERATION = RagGenerationIdentity(3, "a" * 64, 7, 1024)
SNAPSHOT = QuerySnapshot(GENERATION, GlobalScope(), (101,), "deadline")
QUERY = RagQuery("What is the deadline?", GlobalScope())


def _evidence(text: str = "The deadline is Friday.") -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id="ev-1",
        chunk_key="chunk-1",
        exact_text=text,
        heading=None,
        document_version_id=101,
        document_title="source.pdf",
        course_id=11,
        source_spans=(RagChunkSourceSpanValue(1, 1, 0, len(text), 0, len(text)),),
        dense_rank=1,
        lexical_rank=1,
        fused_rank=1,
        generation_id=GENERATION.generation_id,
        content_revision=GENERATION.content_revision,
        token_count=3,
    )


EVIDENCE = (_evidence(),)
CANDIDATES = RetrievalCandidateSet(
    snapshot=SNAPSHOT,
    dense_hits=(DenseSearchHit("chunk-1", 1, 0.1),),
    lexical_result=LexicalSearchResult(
        normalized_query="deadline",
        hits=(LexicalSearchHit("chunk-1", 101, 1, -1.0),),
    ),
    embedding_release_verified=True,
)


def _output(
    *,
    status: RagAnswerStatus = RagAnswerStatus.ANSWER,
    claims: tuple[object, ...] = (),
) -> GroundedModelOutput:
    resolved_claims = claims or (RagClaim("claim-1", "The deadline is Friday.", ("ev-1",)),)
    return GroundedModelOutput(
        status=status,
        answer_text="Friday.",
        language="en",
        claims=resolved_claims,  # type: ignore[arg-type]
        generation_id=GENERATION.generation_id,
        content_revision=GENERATION.content_revision,
        policy_version="grounded-v1",
    )


class FakeRetriever:
    def __init__(self, candidate_set: RetrievalCandidateSet = CANDIDATES) -> None:
        self.candidate_set = candidate_set
        self.calls = 0
        self.embedding_released = False
        self.seen_query: RagQuery | None = None
        self.error: BaseException | None = None

    def retrieve_candidates(self, query: RagQuery) -> RetrievalCandidateSet:
        self.calls += 1
        self.seen_query = query
        self.embedding_released = True
        if self.error is not None:
            raise self.error
        return self.candidate_set


class FakeSelector:
    def __init__(self, evidence: tuple[RetrievedEvidence, ...] = EVIDENCE) -> None:
        self.evidence = evidence
        self.calls = 0
        self.seen_snapshot: QuerySnapshot | None = None
        self.error: BaseException | None = None

    def select(self, snapshot: QuerySnapshot, _candidates: object) -> tuple[RetrievedEvidence, ...]:
        self.calls += 1
        self.seen_snapshot = snapshot
        if self.error is not None:
            raise self.error
        return self.evidence


class FakeGate:
    def __init__(self, status: SufficiencyStatus = SufficiencyStatus.SUPPORTED) -> None:
        self.status = status
        self.calls = 0
        self.seen_snapshot: QuerySnapshot | None = None
        self.error: BaseException | None = None

    def evaluate(
        self, evidence: tuple[RetrievedEvidence, ...], *, snapshot: QuerySnapshot
    ) -> SufficiencyDecision:
        self.calls += 1
        self.seen_snapshot = snapshot
        if self.error is not None:
            raise self.error
        return SufficiencyDecision(
            self.status,
            len(evidence),
            sum(item.token_count for item in evidence),
            "conservative-v1",
        )


class FakeGenerator:
    def __init__(self, retriever: FakeRetriever, output: GroundedModelOutput) -> None:
        self.retriever = retriever
        self.output = output
        self.calls = 0
        self.seen_request: GroundedAnswerRequest | None = None
        self.error: BaseException | None = None

    async def generate(
        self, request: GroundedAnswerRequest, _sufficiency: SufficiencyDecision
    ) -> GroundedModelOutput:
        self.calls += 1
        self.seen_request = request
        if not self.retriever.embedding_released:
            raise AssertionError("generation started before embedding release")
        if self.error is not None:
            raise self.error
        return self.output


class FakeSemanticClassifier:
    def __init__(self, status: SemanticCoverageStatus = SemanticCoverageStatus.SUPPORTED) -> None:
        self.status = status
        self.calls = 0
        self.seen_request: GroundedAnswerRequest | None = None
        self.error: BaseException | None = None

    async def classify(self, request: GroundedAnswerRequest) -> SemanticCoverageDecision:
        self.calls += 1
        self.seen_request = request
        if self.error is not None:
            raise self.error
        return SemanticCoverageDecision(
            status=self.status,
            unsupported_aspects=("unsupported portion",)
            if self.status is SemanticCoverageStatus.PARTIAL
            else (),
            generation_id=request.generation_id,
            content_revision=request.content_revision,
            policy_version=request.policy_version,
        )


class FakeAtomicDecomposer:
    def __init__(self) -> None:
        self.calls = 0

    async def decompose(self, _query: RagQuery) -> AtomicRequirementDecomposition:
        self.calls += 1
        return AtomicRequirementDecomposition(
            AtomicDecompositionStatus.OK,
            (Requirement("r1", "Give the deadline"),),
        )


class FakeSpanExtractor:
    def __init__(self, quote: str = "The deadline") -> None:
        self.calls = 0
        self.quote = quote

    async def extract(
        self, _requirements: RequirementSet, *, evidence: tuple[RetrievedEvidence, ...]
    ) -> tuple[RequirementEvidenceSpans, ...]:
        self.calls += 1
        return (
            RequirementEvidenceSpans("r1", (EvidenceSpan(evidence[0].evidence_id, self.quote),)),
        )


class FakeEntailmentVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(
        self, _requirement: Requirement, _spans: RequirementEvidenceSpans
    ) -> EntailmentResult:
        self.calls += 1
        return EntailmentResult.ENTAILED


class FakeGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, "fake", ("fake",), (4096,))

    async def generate(self, _request: ChatRequest) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            content=(
                '{"status":"ANSWER","answer_text":"Friday.","language":"en",'
                '"claims":[{"claim_id":"claim-1","text":"The deadline is Friday.",'
                '"evidence_ids":["ev-1"]}]}'
            ),
            model_identifier="fake",
        )


def _service(
    *,
    retriever: FakeRetriever | None = None,
    selector: FakeSelector | None = None,
    gate: FakeGate | None = None,
    generator: FakeGenerator | None = None,
    semantic_classifier: FakeSemanticClassifier | None = None,
) -> tuple[GuardedLocalRagService, FakeRetriever, FakeSelector, FakeGate, FakeGenerator]:
    selected_retriever = retriever or FakeRetriever()
    selected_selector = selector or FakeSelector()
    selected_gate = gate or FakeGate()
    selected_generator = generator or FakeGenerator(selected_retriever, _output())
    service = GuardedLocalRagService(
        retriever=selected_retriever,
        evidence_selector=selected_selector,
        sufficiency_gate=selected_gate,
        answer_generator=selected_generator,
        citation_validator=CitationValidator(),
        semantic_classifier=semantic_classifier or FakeSemanticClassifier(),
    )
    return service, selected_retriever, selected_selector, selected_gate, selected_generator


def _ask(service: GuardedLocalRagService, query: RagQuery = QUERY) -> RenderedRagResponse:
    return asyncio.run(service.ask(query))


def test_atomic_path_requires_snapshot_authorized_evidence_and_passes_validated_spans() -> None:
    retriever = FakeRetriever()
    selector = FakeSelector()
    gate = FakeGate()
    generator = FakeGenerator(retriever, _output())
    decomposer = FakeAtomicDecomposer()
    extractor = FakeSpanExtractor()
    verifier = FakeEntailmentVerifier()
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=selector,
        sufficiency_gate=gate,
        answer_generator=generator,
        citation_validator=CitationValidator(),
        atomic_requirement_decomposer=decomposer,
        evidence_span_extractor=extractor,
        entailment_verifier=verifier,
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert decomposer.calls == extractor.calls == verifier.calls == 1
    assert generator.seen_request is not None
    assert generator.seen_request.validated_requirement_spans

    selector.evidence = (replace(_evidence(), document_version_id=999),)
    with pytest.raises(RagOperationalError) as error:
        _ask(service)
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_atomic_path_rejects_forged_span_quote_before_entailment() -> None:
    retriever = FakeRetriever()
    selector = FakeSelector()
    verifier = FakeEntailmentVerifier()
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=selector,
        sufficiency_gate=FakeGate(),
        answer_generator=FakeGenerator(retriever, _output()),
        citation_validator=CitationValidator(),
        atomic_requirement_decomposer=FakeAtomicDecomposer(),
        evidence_span_extractor=FakeSpanExtractor("fabricated quote"),
        entailment_verifier=verifier,
    )

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.PROVENANCE_RESOLUTION_FAILED
    assert verifier.calls == 0


def test_atomic_path_discards_invalid_span_candidates_before_entailment() -> None:
    retriever = FakeRetriever()
    verifier = FakeEntailmentVerifier()

    class MixedSpanExtractor(FakeSpanExtractor):
        async def extract(
            self, _requirements: RequirementSet, *, evidence: tuple[RetrievedEvidence, ...]
        ) -> tuple[RequirementEvidenceSpans, ...]:
            self.calls += 1
            return (
                RequirementEvidenceSpans(
                    "r1",
                    (
                        EvidenceSpan(evidence[0].evidence_id, "The deadline"),
                        EvidenceSpan(evidence[0].evidence_id, "fabricated"),
                    ),
                ),
            )

    extractor = MixedSpanExtractor()
    generator = FakeGenerator(retriever, _output())
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=generator,
        citation_validator=CitationValidator(),
        atomic_requirement_decomposer=FakeAtomicDecomposer(),
        evidence_span_extractor=extractor,
        entailment_verifier=verifier,
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert verifier.calls == 1
    assert generator.seen_request is not None
    assert generator.seen_request.validated_requirement_spans[0].spans == (
        EvidenceSpan("ev-1", "The deadline"),
    )


def test_atomic_path_allows_distinct_spans_with_same_evidence_id() -> None:
    retriever = FakeRetriever()
    verifier = FakeEntailmentVerifier()

    class DistinctSpanExtractor(FakeSpanExtractor):
        async def extract(
            self, _requirements: RequirementSet, *, evidence: tuple[RetrievedEvidence, ...]
        ) -> tuple[RequirementEvidenceSpans, ...]:
            self.calls += 1
            return (
                RequirementEvidenceSpans(
                    "r1",
                    (
                        EvidenceSpan(evidence[0].evidence_id, "The deadline"),
                        EvidenceSpan(evidence[0].evidence_id, "deadline is Friday"),
                    ),
                ),
            )

    generator = FakeGenerator(retriever, _output())
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=generator,
        citation_validator=CitationValidator(),
        atomic_requirement_decomposer=FakeAtomicDecomposer(),
        evidence_span_extractor=DistinctSpanExtractor(),
        entailment_verifier=verifier,
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert generator.seen_request is not None
    assert len(generator.seen_request.validated_requirement_spans[0].spans) == 2


def test_success_composes_one_snapshot_and_trusted_rendering() -> None:
    service, retriever, selector, gate, generator = _service()

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert result.answer.citations == (Citation("claim-1", 101, "source.pdf", 1, 1),)
    assert result.rendered_text.startswith("The deadline is Friday.")
    assert retriever.calls == selector.calls == gate.calls == generator.calls == 1
    assert selector.seen_snapshot is retriever.candidate_set.snapshot
    assert gate.seen_snapshot is retriever.candidate_set.snapshot
    assert generator.seen_request is not None
    assert generator.seen_request.evidence == EVIDENCE


def test_partial_status_is_preserved_by_the_facade() -> None:
    retriever = FakeRetriever()
    gate = FakeGate(SufficiencyStatus.PARTIALLY_SUPPORTED)
    generator = FakeGenerator(retriever, _output(status=RagAnswerStatus.PARTIAL_ANSWER))
    service, *_ = _service(retriever=retriever, gate=gate, generator=generator)

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.PARTIAL_ANSWER
    assert "unsupported" in result.rendered_text


def test_unsupported_sufficiency_never_calls_answer_gateway() -> None:
    retriever = FakeRetriever()
    selector = FakeSelector(())
    gate = FakeGate(SufficiencyStatus.UNSUPPORTED)
    gateway = FakeGateway()
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=selector,
        sufficiency_gate=gate,
        answer_generator=GroundedAnswerGenerator(gateway),
        citation_validator=CitationValidator(),
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert gateway.calls == 0


def test_structural_unsupported_bypasses_semantic_classifier() -> None:
    retriever = FakeRetriever()
    selector = FakeSelector(())
    gate = FakeGate(SufficiencyStatus.UNSUPPORTED)

    class MustNotClassify:
        async def classify(self, _request: GroundedAnswerRequest) -> SemanticCoverageDecision:
            raise AssertionError("semantic classifier must not run after structural refusal")

    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=selector,
        sufficiency_gate=gate,
        answer_generator=FakeGenerator(retriever, _output()),
        citation_validator=CitationValidator(),
        semantic_classifier=MustNotClassify(),
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE


def test_semantic_unsupported_skips_factual_generation() -> None:
    retriever = FakeRetriever()
    classifier = FakeSemanticClassifier(SemanticCoverageStatus.UNSUPPORTED)
    generator = FakeGenerator(retriever, _output())
    service, *_ = _service(
        retriever=retriever,
        generator=generator,
        semantic_classifier=classifier,
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert classifier.calls == 1
    assert generator.calls == 0


def test_semantic_partial_status_reaches_generator_as_constraint() -> None:
    retriever = FakeRetriever()
    classifier = FakeSemanticClassifier(SemanticCoverageStatus.PARTIAL)
    generator = FakeGenerator(retriever, _output(status=RagAnswerStatus.PARTIAL_ANSWER))
    service, *_ = _service(
        retriever=retriever,
        generator=generator,
        semantic_classifier=classifier,
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.PARTIAL_ANSWER
    assert generator.seen_request is not None
    assert generator.seen_request.unsupported_aspects == ("unsupported portion",)


def test_semantic_classifier_failure_stops_before_factual_generation() -> None:
    retriever = FakeRetriever()
    classifier = FakeSemanticClassifier()
    classifier.error = RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE)
    generator = FakeGenerator(retriever, _output())
    service, *_ = _service(
        retriever=retriever,
        generator=generator,
        semantic_classifier=classifier,
    )

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.MODEL_UNAVAILABLE
    assert generator.calls == 0


def test_retriever_error_is_preserved_without_generation_fallback() -> None:
    retriever = FakeRetriever()
    retriever.error = RagOperationalError(RagErrorCode.DENSE_INDEX_UNAVAILABLE)
    service, _, _, _, generator = _service(retriever=retriever)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.DENSE_INDEX_UNAVAILABLE
    assert generator.calls == 0


def test_lexical_retrieval_error_is_preserved() -> None:
    retriever = FakeRetriever()
    retriever.error = RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED)
    service, *_ = _service(retriever=retriever)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.LEXICAL_INDEX_FAILED


def test_generation_error_is_preserved_after_embedding_release() -> None:
    retriever = FakeRetriever()
    generator = FakeGenerator(retriever, _output())
    generator.error = RagOperationalError(RagErrorCode.MODEL_TIMEOUT)
    service, *_ = _service(retriever=retriever, generator=generator)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.MODEL_TIMEOUT
    assert retriever.embedding_released is True


def test_unverified_embedding_release_fails_before_generation() -> None:
    retriever = FakeRetriever()
    retriever.candidate_set = RetrievalCandidateSet(
        snapshot=SNAPSHOT,
        dense_hits=CANDIDATES.dense_hits,
        lexical_result=CANDIDATES.lexical_result,
    )
    service, _, _, _, generator = _service(retriever=retriever)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY
    assert generator.calls == 0


def test_selector_failure_stops_before_generation() -> None:
    retriever = FakeRetriever()
    selector = FakeSelector()
    selector.error = RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    service, _, _, _, generator = _service(retriever=retriever, selector=selector)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY
    assert generator.calls == 0


def test_sufficiency_failure_stops_before_generation() -> None:
    gate = FakeGate()
    gate.error = RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
    service, _, _, _, generator = _service(gate=gate)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY
    assert generator.calls == 0


def test_citation_validation_failure_is_safe() -> None:
    class FailingValidator:
        def validate(self, _output: object, _request: object) -> object:
            raise RuntimeError("unsafe validator detail")

    retriever = FakeRetriever()
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=FakeGenerator(retriever, _output()),
        citation_validator=FailingValidator(),  # type: ignore[arg-type]
        semantic_classifier=FakeSemanticClassifier(),
    )

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.INVALID_STRUCTURED_OUTPUT
    assert "unsafe validator detail" not in str(error.value)


def test_render_failure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    service, *_ = _service()

    def fail_render(_answer: object) -> object:
        raise RuntimeError("unsafe renderer detail")

    monkeypatch.setattr("aaos.rag.service.render_rag_answer", fail_render)

    with pytest.raises(RagOperationalError) as error:
        _ask(service)

    assert error.value.code is RagErrorCode.INVALID_STRUCTURED_OUTPUT
    assert "unsafe renderer detail" not in str(error.value)


def test_invalid_query_fails_closed_before_retrieval() -> None:
    service, retriever, *_ = _service()

    with pytest.raises(RagOperationalError) as error:
        _ask(service, object())  # type: ignore[arg-type]

    assert error.value.code is RagErrorCode.INVALID_QUERY_SCOPE
    assert retriever.calls == 0


def test_facade_has_no_history_tools_or_browser_surface() -> None:
    service, *_ = _service()
    import aaos.rag as rag

    assert hasattr(service, "ask")
    assert rag.GuardedLocalRagService is GuardedLocalRagService
    assert not hasattr(service, "history")
    assert not hasattr(service, "tools")
    assert not hasattr(service, "browser")


def test_claim_verification_occurs_after_generation_and_before_citation_validation() -> None:
    events: list[str] = []
    retriever = FakeRetriever()

    class RecordingRequirementDecomposer:
        async def decompose(self, _query: RagQuery) -> RequirementSet:
            return RequirementSet((Requirement("r1", "Give the deadline"),))

    class RecordingRequirementCoverageClassifier:
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
        ) -> RequirementCoverageDecision:
            del question, answer_language, evidence
            events.append("coverage")
            return RequirementCoverageDecision(
                requirements,
                (
                    RequirementCoverageJudgment(
                        "r1",
                        RequirementSupport.SUPPORTED,
                        ("ev-1",),
                    ),
                ),
                SemanticCoverageStatus.SUPPORTED,
                generation_id,
                content_revision,
                policy_version,
            )

    class RecordingGenerator(FakeGenerator):
        async def generate(
            self,
            request: GroundedAnswerRequest,
            sufficiency: SufficiencyDecision,
        ) -> GroundedModelOutput:
            assert events == ["coverage"]
            events.append("generation")
            return await super().generate(request, sufficiency)

    class RecordingClaimVerifier:
        async def verify(
            self,
            claim: RagClaim,
            *,
            evidence: tuple[RetrievedEvidence, ...],
            validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
        ) -> EntailmentResult:
            del validated_requirement_spans
            assert events == ["coverage", "generation"]
            assert claim.text == "The deadline is Friday."
            assert claim.evidence_ids == ("ev-1",)
            assert evidence == EVIDENCE
            events.append("claim_nli")
            return EntailmentResult.ENTAILED

    class RecordingCitationValidator:
        def __init__(self) -> None:
            self._inner = CitationValidator()

        def validate(
            self,
            output: GroundedModelOutput,
            request: GroundedAnswerRequest,
        ) -> RagAnswer:
            assert events == ["coverage", "generation", "claim_nli"]
            events.append("citation_validation")
            return self._inner.validate(output, request)

    generator = RecordingGenerator(retriever, _output())
    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=generator,
        citation_validator=RecordingCitationValidator(),
        requirement_decomposer=RecordingRequirementDecomposer(),
        requirement_coverage_classifier=RecordingRequirementCoverageClassifier(),
        claim_entailment_verifier=RecordingClaimVerifier(),
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert events == [
        "coverage",
        "generation",
        "claim_nli",
        "citation_validation",
    ]


def test_citation_validation_receives_only_entailed_generated_claims() -> None:
    retriever = FakeRetriever()
    received_claims: list[tuple[RagClaim, ...]] = []

    class RecordingRequirementDecomposer:
        async def decompose(self, _query: RagQuery) -> RequirementSet:
            return RequirementSet((Requirement("r1", "Give the deadline"),))

    class RecordingRequirementCoverageClassifier:
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
        ) -> RequirementCoverageDecision:
            del question, answer_language, evidence
            return RequirementCoverageDecision(
                requirements,
                (
                    RequirementCoverageJudgment(
                        "r1",
                        RequirementSupport.SUPPORTED,
                        ("ev-1",),
                    ),
                ),
                SemanticCoverageStatus.SUPPORTED,
                generation_id,
                content_revision,
                policy_version,
            )

    entailed_claim = RagClaim(
        claim_id="claim-1",
        text="The deadline is Friday.",
        evidence_ids=("ev-1",),
    )
    unsupported_claim = RagClaim(
        claim_id="claim-2",
        text="The deadline is Monday.",
        evidence_ids=("ev-1",),
    )

    generated = GroundedModelOutput(
        status=RagAnswerStatus.ANSWER,
        answer_text="Friday. Monday.",
        language="en",
        claims=(entailed_claim, unsupported_claim),
        generation_id=3,
        content_revision=7,
        policy_version="grounded-v1",
    )

    class RecordingClaimVerifier:
        async def verify(
            self,
            claim: RagClaim,
            *,
            evidence: tuple[RetrievedEvidence, ...],
            validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
        ) -> EntailmentResult:
            del validated_requirement_spans
            assert evidence == EVIDENCE
            if claim.claim_id == "claim-1":
                return EntailmentResult.ENTAILED
            if claim.claim_id == "claim-2":
                return EntailmentResult.NOT_ENTAILED
            raise AssertionError(f"unexpected claim: {claim.claim_id}")

    class SurvivorOnlyCitationValidator:
        def __init__(self) -> None:
            self._inner = CitationValidator()

        def validate(
            self,
            output: GroundedModelOutput,
            request: GroundedAnswerRequest,
        ) -> RagAnswer:
            received_claims.append(output.claims)
            return self._inner.validate(output, request)

    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=FakeGenerator(retriever, generated),
        citation_validator=SurvivorOnlyCitationValidator(),
        requirement_decomposer=RecordingRequirementDecomposer(),
        requirement_coverage_classifier=RecordingRequirementCoverageClassifier(),
        claim_entailment_verifier=RecordingClaimVerifier(),
    )

    result = _ask(service)

    assert received_claims == [(entailed_claim,)]
    assert result.answer.status is RagAnswerStatus.PARTIAL_ANSWER
    assert result.answer.answer_text == "The deadline is Friday."
    assert "Monday" not in result.answer.answer_text


def test_zero_entailed_generated_claims_project_to_insufficient_evidence() -> None:
    retriever = FakeRetriever()
    received_outputs: list[GroundedModelOutput] = []

    class RecordingRequirementDecomposer:
        async def decompose(self, _query: RagQuery) -> RequirementSet:
            return RequirementSet((Requirement("r1", "Give the deadline"),))

    class RecordingRequirementCoverageClassifier:
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
        ) -> RequirementCoverageDecision:
            del question, answer_language, evidence
            return RequirementCoverageDecision(
                requirements,
                (
                    RequirementCoverageJudgment(
                        "r1",
                        RequirementSupport.SUPPORTED,
                        ("ev-1",),
                    ),
                ),
                SemanticCoverageStatus.SUPPORTED,
                generation_id,
                content_revision,
                policy_version,
            )

    unsupported_claim = RagClaim(
        claim_id="claim-1",
        text="The deadline is Monday.",
        evidence_ids=("ev-1",),
    )

    generated = GroundedModelOutput(
        status=RagAnswerStatus.ANSWER,
        answer_text="The deadline is Monday.",
        language="en",
        claims=(unsupported_claim,),
        generation_id=3,
        content_revision=7,
        policy_version="grounded-v1",
    )

    class RejectingClaimVerifier:
        async def verify(
            self,
            claim: RagClaim,
            *,
            evidence: tuple[RetrievedEvidence, ...],
            validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
        ) -> EntailmentResult:
            del validated_requirement_spans
            assert claim == unsupported_claim
            assert evidence == EVIDENCE
            return EntailmentResult.NOT_ENTAILED

    class RecordingCitationValidator:
        def __init__(self) -> None:
            self._inner = CitationValidator()

        def validate(
            self,
            output: GroundedModelOutput,
            request: GroundedAnswerRequest,
        ) -> RagAnswer:
            received_outputs.append(output)

            return self._inner.validate(output, request)

    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=FakeGenerator(retriever, generated),
        citation_validator=RecordingCitationValidator(),
        requirement_decomposer=RecordingRequirementDecomposer(),
        requirement_coverage_classifier=RecordingRequirementCoverageClassifier(),
        claim_entailment_verifier=RejectingClaimVerifier(),
    )

    result = _ask(service)

    assert len(received_outputs) == 1
    projected = received_outputs[0]

    assert projected.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert projected.claims == ()
    assert projected.answer_text == "Insufficient evidence."

    assert result.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer.claims == ()
    assert result.answer.citations == ()


def test_atomic_path_uses_question_aware_coverage_after_validated_spans() -> None:
    events: list[str] = []
    retriever = FakeRetriever()

    class InterrogativeAtomicDecomposer:
        async def decompose(self, _query: RagQuery) -> AtomicRequirementDecomposition:
            events.append("decompose")
            return AtomicRequirementDecomposition(
                AtomicDecompositionStatus.OK,
                (Requirement("r1", "When is the deadline?"),),
            )

    class RecordingSpanExtractor(FakeSpanExtractor):
        async def extract(
            self,
            requirements: RequirementSet,
            *,
            evidence: tuple[RetrievedEvidence, ...],
        ) -> tuple[RequirementEvidenceSpans, ...]:
            assert events == ["decompose"]
            assert requirements.requirements[0].requirement_text == "When is the deadline?"
            events.append("spans")
            return await super().extract(requirements, evidence=evidence)

    class RecordingCoverageClassifier:
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
        ) -> RequirementCoverageDecision:
            assert events == ["decompose", "spans"]
            assert requirements.requirements[0].requirement_text == "When is the deadline?"
            assert question
            assert answer_language
            assert evidence == EVIDENCE
            events.append("coverage")

            return RequirementCoverageDecision(
                requirements,
                (
                    RequirementCoverageJudgment(
                        "r1",
                        RequirementSupport.SUPPORTED,
                        ("ev-1",),
                    ),
                ),
                SemanticCoverageStatus.SUPPORTED,
                generation_id,
                content_revision,
                policy_version,
            )

    class RecordingGenerator(FakeGenerator):
        async def generate(
            self,
            request: GroundedAnswerRequest,
            sufficiency: SufficiencyDecision,
        ) -> GroundedModelOutput:
            assert events == ["decompose", "spans", "coverage"]
            assert request.validated_requirement_spans
            events.append("generation")
            return await super().generate(request, sufficiency)

    class RecordingClaimVerifier:
        async def verify(
            self,
            claim: RagClaim,
            *,
            evidence: tuple[RetrievedEvidence, ...],
            validated_requirement_spans: tuple[RequirementEvidenceSpans, ...] = (),
        ) -> EntailmentResult:
            assert events == ["decompose", "spans", "coverage", "generation"]
            assert claim.text == "The deadline is Friday."
            assert evidence == EVIDENCE
            assert validated_requirement_spans == (
                RequirementEvidenceSpans(
                    "r1",
                    (
                        EvidenceSpan(
                            "ev-1",
                            "The deadline",
                        ),
                    ),
                ),
            )
            events.append("claim_nli")
            return EntailmentResult.ENTAILED

    generator = RecordingGenerator(retriever, _output())

    service = GuardedLocalRagService(
        retriever=retriever,
        evidence_selector=FakeSelector(),
        sufficiency_gate=FakeGate(),
        answer_generator=generator,
        citation_validator=CitationValidator(),
        atomic_requirement_decomposer=InterrogativeAtomicDecomposer(),
        evidence_span_extractor=RecordingSpanExtractor(),
        requirement_coverage_classifier=RecordingCoverageClassifier(),
        claim_entailment_verifier=RecordingClaimVerifier(),
    )

    result = _ask(service)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert events == [
        "decompose",
        "spans",
        "coverage",
        "generation",
        "claim_nli",
    ]


def test_ask_reports_progress_stages_in_pipeline_order() -> None:
    from aaos.ask_progress import ASK_STAGE_REPORTER, AskStage

    service, *_ = _service()
    stages: list[AskStage] = []
    token = ASK_STAGE_REPORTER.set(stages.append)
    try:
        result = _ask(service)
    finally:
        ASK_STAGE_REPORTER.reset(token)

    assert result.answer.status is RagAnswerStatus.ANSWER
    assert stages == [
        AskStage.SEARCHING,
        AskStage.CHECKING_COVERAGE,
        AskStage.WRITING,
        AskStage.VERIFYING,
    ]


def test_unsupported_answer_skips_the_writing_stage_but_is_still_verified() -> None:
    from aaos.ask_progress import ASK_STAGE_REPORTER, AskStage

    retriever = FakeRetriever()
    service, *_ = _service(
        retriever=retriever,
        selector=FakeSelector(()),
        gate=FakeGate(SufficiencyStatus.UNSUPPORTED),
    )
    stages: list[AskStage] = []
    token = ASK_STAGE_REPORTER.set(stages.append)
    try:
        result = _ask(service)
    finally:
        ASK_STAGE_REPORTER.reset(token)

    assert result.answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert AskStage.WRITING not in stages
    assert stages[-1] is AskStage.VERIFYING
