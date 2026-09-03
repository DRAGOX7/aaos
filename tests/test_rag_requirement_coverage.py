"""TDD coverage for the approved two-step requirement semantic boundary."""

from __future__ import annotations

import asyncio
import json

import pytest

from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.rag.contracts import (
    DenseSearchHit,
    GlobalScope,
    GroundedAnswerRequest,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagClaim,
    RagGenerationIdentity,
    RagOperationalError,
    RagQuery,
    Requirement,
    RequirementCoverageDecision,
    RequirementCoverageJudgment,
    RequirementSet,
    RequirementSupport,
    RetrievedEvidence,
    SemanticCoverageStatus,
)
from aaos.rag.generation import GroundedAnswerGenerator, GroundedModelOutput
from aaos.rag.lexical import LexicalSearchResult
from aaos.rag.retrieval import (
    QuerySnapshot,
    RetrievalCandidateSet,
    SufficiencyDecision,
    SufficiencyStatus,
)
from aaos.rag.semantic_coverage import (
    RequirementCoverageClassifier,
    RequirementDecomposer,
    aggregate_requirement_coverage,
)
from aaos.rag.service import LocalRagService

GENERATION = RagGenerationIdentity(2, "a" * 64, 4, 1024)


def _evidence(
    evidence_id: str = "ev-1", text: str = "The deadline is Friday."
) -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=evidence_id,
        chunk_key=f"chunk-{evidence_id}",
        exact_text=text,
        heading=None,
        document_version_id=10,
        document_title="source.pdf",
        course_id=3,
        source_spans=(RagChunkSourceSpanValue(1, 1, 0, len(text), 0, len(text)),),
        dense_rank=1,
        lexical_rank=1,
        fused_rank=1,
        generation_id=GENERATION.generation_id,
        content_revision=GENERATION.content_revision,
        token_count=3,
    )


class FakeGateway:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, "qwen/qwen3.5-4b", ("qwen/qwen3.5-4b",), (8192,))

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(content=self.responses.pop(0), model_identifier="qwen/qwen3.5-4b")


def _decompose(gateway: FakeGateway) -> RequirementSet:
    return asyncio.run(
        RequirementDecomposer(gateway).decompose(RagQuery("What is the deadline?", GlobalScope()))
    )


def test_decomposition_assigns_canonical_ids_without_evidence() -> None:
    gateway = FakeGateway(
        [
            '{"requirements":[{"requirement_text":"Give the deadline"},{"requirement_text":"Give the location"}]}'
        ]
    )

    result = _decompose(gateway)

    assert tuple(item.requirement_id for item in result.requirements) == ("r1", "r2")
    assert all(
        "The deadline is Friday" not in message.content for message in gateway.requests[0].messages
    )
    assert gateway.requests[0].structured_output_schema == "requirement_decomposition"
    assert gateway.requests[0].max_output_tokens == 512


def test_decomposition_uses_one_same_schema_repair_at_most() -> None:
    valid = '{"requirements":[{"requirement_text":"Give the deadline"}]}'
    gateway = FakeGateway(["{}", valid, valid])

    result = _decompose(gateway)

    assert tuple(item.requirement_id for item in result.requirements) == ("r1",)
    assert len(gateway.requests) == 2
    assert all(
        request.structured_output_schema == "requirement_decomposition"
        and request.max_output_tokens == 512
        for request in gateway.requests
    )


def test_decomposition_repair_failure_is_closed_after_two_calls() -> None:
    gateway = FakeGateway(["{}", "{}", '{"requirements":[]}'])

    with pytest.raises(RagOperationalError):
        _decompose(gateway)

    assert len(gateway.requests) == 2


def test_coverage_requires_exact_ids_but_normalizes_permuted_order() -> None:
    gateway = FakeGateway(
        [
            '{"requirements":[{"requirement_id":"r2","support":"UNSUPPORTED","evidence_ids":[]},{"requirement_id":"r1","support":"SUPPORTED","evidence_ids":["ev-1"]}]}'
        ]
    )
    requirements = _decompose(
        FakeGateway(
            [
                '{"requirements":[{"requirement_text":"Give the deadline"},{"requirement_text":"Give the location"}]}'
            ]
        )
    )

    result = asyncio.run(
        RequirementCoverageClassifier(gateway).classify(
            requirements,
            question="Give the deadline and location",
            answer_language="en",
            generation_id=GENERATION.generation_id,
            content_revision=GENERATION.content_revision,
            policy_version="grounded-v1",
            evidence=(_evidence(),),
        )
    )

    assert result.status is SemanticCoverageStatus.PARTIAL
    assert tuple(item.requirement_id for item in result.judgments) == ("r1", "r2")
    assert gateway.requests[0].structured_output_schema == "requirement_coverage"
    assert gateway.requests[0].max_output_tokens == 512


def test_coverage_uses_one_same_schema_repair_at_most() -> None:
    requirements = _decompose(
        FakeGateway(['{"requirements":[{"requirement_text":"Give the deadline"}]}'])
    )
    valid = (
        '{"requirements":[{"requirement_id":"r1","support":"SUPPORTED","evidence_ids":["ev-1"]}]}'
    )
    gateway = FakeGateway(["{}", valid, valid])

    result = asyncio.run(
        RequirementCoverageClassifier(gateway).classify(
            requirements,
            question="Give the deadline",
            answer_language="en",
            generation_id=GENERATION.generation_id,
            content_revision=GENERATION.content_revision,
            policy_version="grounded-v1",
            evidence=(_evidence(),),
        )
    )

    assert result.status is SemanticCoverageStatus.SUPPORTED
    assert len(gateway.requests) == 2
    assert all(
        request.structured_output_schema == "requirement_coverage"
        and request.max_output_tokens == 512
        for request in gateway.requests
    )


def test_coverage_invalid_provenance_fails_closed_before_model_output() -> None:
    evidence = _evidence()
    object.__setattr__(evidence, "source_spans", ())
    requirements = _decompose(
        FakeGateway(['{"requirements":[{"requirement_text":"Give the deadline"}]}'])
    )
    gateway = FakeGateway(
        ['{"requirements":[{"requirement_id":"r1","support":"SUPPORTED","evidence_ids":["ev-1"]}]}']
    )

    with pytest.raises(RagOperationalError):
        asyncio.run(
            RequirementCoverageClassifier(gateway).classify(
                requirements,
                question="Give the deadline",
                answer_language="en",
                generation_id=GENERATION.generation_id,
                content_revision=GENERATION.content_revision,
                policy_version="grounded-v1",
                evidence=(evidence,),
            )
        )

    assert gateway.requests == []


def test_coverage_aggregation_is_code_owned() -> None:
    supported = RequirementCoverageJudgment("r1", RequirementSupport.SUPPORTED, ("ev-1",))
    unsupported = RequirementCoverageJudgment("r2", RequirementSupport.UNSUPPORTED, ())

    assert aggregate_requirement_coverage((supported,)) is SemanticCoverageStatus.SUPPORTED
    assert aggregate_requirement_coverage((unsupported,)) is SemanticCoverageStatus.UNSUPPORTED
    assert (
        aggregate_requirement_coverage((supported, unsupported)) is SemanticCoverageStatus.PARTIAL
    )


def test_coverage_decision_rejects_model_owned_aggregate_status() -> None:
    requirements = RequirementSet((Requirement("r1", "Give the deadline"),))

    with pytest.raises(ValueError):
        RequirementCoverageDecision(
            requirements,
            (RequirementCoverageJudgment("r1", RequirementSupport.SUPPORTED, ("ev-1",)),),
            SemanticCoverageStatus.UNSUPPORTED,
            GENERATION.generation_id,
            GENERATION.content_revision,
            "grounded-v1",
        )


def test_partial_generation_receives_only_supported_evidence() -> None:
    requirements = RequirementSet(
        (
            Requirement("r1", "Give the deadline"),
            Requirement("r2", "Give the location"),
        )
    )
    coverage = RequirementCoverageDecision(
        requirements,
        (
            RequirementCoverageJudgment("r1", RequirementSupport.SUPPORTED, ("ev-1",)),
            RequirementCoverageJudgment("r2", RequirementSupport.UNSUPPORTED, ()),
        ),
        SemanticCoverageStatus.PARTIAL,
        GENERATION.generation_id,
        GENERATION.content_revision,
        "grounded-v1",
    )
    request = GroundedAnswerRequest.from_query(
        RagQuery("Give the deadline and location", GlobalScope()),
        generation_id=GENERATION.generation_id,
        content_revision=GENERATION.content_revision,
        evidence=(_evidence("ev-1"), _evidence("ev-2", "The location is Amman.")),
        policy_version="grounded-v1",
        requirement_coverage=coverage,
    )
    output = (
        '{"status":"PARTIAL_ANSWER","answer_text":"Friday; location unavailable.",'
        '"language":"en","claims":[{"claim_id":"c1","text":"Friday",'
        '"evidence_ids":["ev-1"]}]}'
    )
    gateway = FakeGateway([output])

    asyncio.run(
        GroundedAnswerGenerator(gateway).generate(
            request,
            SufficiencyDecision(SufficiencyStatus.SUPPORTED, 2, 6, "v1"),
        )
    )

    grounded_input = next(
        message.content.split("<grounded-input>", 1)[1].split("</grounded-input>", 1)[0]
        for message in gateway.requests[0].messages
        if "<grounded-input>" in message.content
    )
    payload = json.loads(grounded_input)
    assert [item["evidence_id"] for item in payload["evidence"]] == ["ev-1"]
    assert payload["supported_requirements"] == [
        {"requirement_id": "r1", "requirement_text": "Give the deadline"}
    ]
    assert payload["unsupported_requirements"] == ["Give the location"]


def test_unsupported_requirement_coverage_skips_factual_generation() -> None:
    requirements = RequirementSet((Requirement("r1", "Give the location"),))
    coverage = RequirementCoverageDecision(
        requirements,
        (RequirementCoverageJudgment("r1", RequirementSupport.UNSUPPORTED, ()),),
        SemanticCoverageStatus.UNSUPPORTED,
        GENERATION.generation_id,
        GENERATION.content_revision,
        "grounded-v1",
    )
    request = GroundedAnswerRequest.from_query(
        RagQuery("Give the location", GlobalScope()),
        generation_id=GENERATION.generation_id,
        content_revision=GENERATION.content_revision,
        evidence=(_evidence(),),
        policy_version="grounded-v1",
        requirement_coverage=coverage,
    )
    gateway = FakeGateway([])

    result = asyncio.run(
        GroundedAnswerGenerator(gateway).generate(
            request,
            SufficiencyDecision(SufficiencyStatus.SUPPORTED, 1, 3, "v1"),
        )
    )

    assert result.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert gateway.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"requirements":[{"requirement_id":"r1","support":"SUPPORTED","evidence_ids":["ev-1"]}]}',
        '{"requirements":[{"requirement_id":"r1","support":"UNSUPPORTED","evidence_ids":[]},{"requirement_id":"r1","support":"UNSUPPORTED","evidence_ids":[]}]}',
        '{"requirements":[{"requirement_id":"r3","support":"SUPPORTED","evidence_ids":["ev-1"]}]}',
    ],
)
def test_coverage_integrity_failures_are_closed(payload: str) -> None:
    gateway = FakeGateway([payload, payload])
    requirements = _decompose(
        FakeGateway(
            [
                '{"requirements":[{"requirement_text":"Give the deadline"},{"requirement_text":"Give the location"}]}'
            ]
        )
    )

    with pytest.raises(RagOperationalError):
        asyncio.run(
            RequirementCoverageClassifier(gateway).classify(
                requirements,
                question="Give the deadline and location",
                answer_language="en",
                generation_id=GENERATION.generation_id,
                content_revision=GENERATION.content_revision,
                policy_version="grounded-v1",
                evidence=(_evidence(),),
            )
        )


def test_service_uses_two_step_semantic_boundary_before_generation() -> None:
    requirements = RequirementSet((Requirement("r1", "Give the deadline"),))
    snapshot = QuerySnapshot(GENERATION, GlobalScope(), (10,), "deadline")
    candidates = RetrievalCandidateSet(
        snapshot=snapshot,
        dense_hits=(DenseSearchHit("chunk-ev-1", 1, 0.1),),
        lexical_result=LexicalSearchResult("deadline", ()),
        embedding_release_verified=True,
    )

    class Retriever:
        def retrieve_candidates(self, _query: RagQuery) -> RetrievalCandidateSet:
            return candidates

    class Selector:
        def select(
            self, _snapshot: QuerySnapshot, _candidates: object
        ) -> tuple[RetrievedEvidence, ...]:
            return (_evidence(),)

    class Gate:
        def evaluate(
            self, evidence: tuple[RetrievedEvidence, ...], *, snapshot: QuerySnapshot
        ) -> SufficiencyDecision:
            return SufficiencyDecision(SufficiencyStatus.SUPPORTED, len(evidence), 3, "v1")

    class Decomposer:
        async def decompose(self, _query: RagQuery) -> RequirementSet:
            return requirements

    class Coverage:
        async def classify(
            self,
            _requirements: RequirementSet,
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
                (RequirementCoverageJudgment("r1", RequirementSupport.SUPPORTED, ("ev-1",)),),
                SemanticCoverageStatus.SUPPORTED,
                generation_id,
                content_revision,
                policy_version,
            )

    class Generator:
        async def generate(
            self, request: GroundedAnswerRequest, _sufficiency: SufficiencyDecision
        ) -> GroundedModelOutput:
            return GroundedModelOutput(
                RagAnswerStatus.ANSWER,
                "Friday",
                "en",
                (RagClaim("claim-1", "Friday", ("ev-1",)),),
                request.generation_id,
                request.content_revision,
                request.policy_version,
            )

    service = LocalRagService(
        retriever=Retriever(),
        evidence_selector=Selector(),
        sufficiency_gate=Gate(),
        answer_generator=Generator(),
        requirement_decomposer=Decomposer(),
        requirement_coverage_classifier=Coverage(),
    )
    result = asyncio.run(service.ask(RagQuery("What is the deadline?", GlobalScope())))

    assert result.answer.status is RagAnswerStatus.ANSWER
