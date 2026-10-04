"""The document-answering steps accept the opt-in Gemini model, and only that.

Regression: with Gemini selected, a plain question worked but every question about
course files failed with "The answering model is unavailable". Google answered each
request; the semantic steps then rejected every reply because it was not named
"qwen/qwen3.5-4b", and the rejection was reported as an unavailable model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.rag.contracts import GlobalScope, RagErrorCode, RagOperationalError, RagQuery
from aaos.rag.factory import _semantic_factories_for
from aaos.rag.semantic_coverage import (
    AtomicRequirementDecomposer,
    EvidenceSpanExtractor,
    RequirementCoverageClassifier,
)

GEMINI = "gemini-test-flash"
QWEN = "qwen/qwen3.5-4b"
DECOMPOSITION = json.dumps(
    {"status": "OK", "requirements": [{"requirement_text": "Say what KNIME is."}]}
)


class FakeGateway:
    def __init__(self, *responses: ChatResponse) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, QWEN, (QWEN,), (8192,))

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _reply(model: str, payload: str = DECOMPOSITION) -> ChatResponse:
    return ChatResponse(content=payload, model_identifier=model)


def _decompose(gateway: FakeGateway, **options: object):  # type: ignore[no-untyped-def]
    decomposer = AtomicRequirementDecomposer(gateway, **options)  # type: ignore[arg-type]
    return asyncio.run(decomposer.decompose(RagQuery("What is KNIME?", GlobalScope())))


def test_the_approved_local_model_is_still_the_default_and_the_only_identity() -> None:
    assert _decompose(FakeGateway(_reply(QWEN))).status.value == "OK"

    with pytest.raises(ValueError, match="approved Qwen"):
        AtomicRequirementDecomposer(FakeGateway(), model_identifier=GEMINI)
    with pytest.raises(ValueError, match="approved Qwen"):
        EvidenceSpanExtractor(FakeGateway(), model_identifier=GEMINI)
    with pytest.raises(ValueError, match="approved Qwen"):
        RequirementCoverageClassifier(FakeGateway(), model_identifier=GEMINI)


def test_a_reply_from_gemini_is_rejected_unless_that_model_was_named() -> None:
    """The bug: the same reply that is fine from Qwen was refused when Gemini wrote it."""
    with pytest.raises(RagOperationalError) as refused:
        _decompose(FakeGateway(_reply(GEMINI), _reply(GEMINI)))
    # Reported as an unavailable model, which is what the owner saw on the page.
    assert refused.value.code is RagErrorCode.MODEL_UNAVAILABLE


def test_naming_the_gemini_model_makes_its_replies_acceptable() -> None:
    named = {"model_identifier": GEMINI, "approved_identifiers": frozenset({GEMINI})}

    assert _decompose(FakeGateway(_reply(GEMINI)), **named).status.value == "OK"


def test_a_reply_from_any_other_model_is_still_refused_when_gemini_is_named() -> None:
    named = {"model_identifier": GEMINI, "approved_identifiers": frozenset({GEMINI})}

    with pytest.raises(RagOperationalError):
        _decompose(FakeGateway(_reply(QWEN), _reply(QWEN)), **named)
    with pytest.raises(RagOperationalError):
        _decompose(FakeGateway(_reply("some-other-model"), _reply("some-other-model")), **named)


def test_an_unapproved_identity_cannot_be_smuggled_in_through_the_approved_set() -> None:
    with pytest.raises(ValueError, match="approved Qwen"):
        AtomicRequirementDecomposer(
            FakeGateway(), model_identifier="other", approved_identifiers=frozenset({GEMINI})
        )


def test_the_cloud_composition_builds_all_three_steps_for_the_active_model() -> None:
    factories = _semantic_factories_for(GEMINI)
    gateway = FakeGateway()

    decomposer = factories["atomic_requirement_decomposer_factory"](gateway)  # type: ignore[operator]
    extractor = factories["evidence_span_extractor_factory"](gateway)  # type: ignore[operator]
    coverage = factories["requirement_coverage_factory"](gateway)  # type: ignore[operator]

    for step in (decomposer, extractor, coverage):
        assert step._model_identifier == GEMINI
    assert isinstance(decomposer, AtomicRequirementDecomposer)
    assert isinstance(extractor, EvidenceSpanExtractor)
    assert isinstance(coverage, RequirementCoverageClassifier)
