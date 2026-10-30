"""A same-message explanation pronoun is not a previous-record identifier."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_agent_academic_orchestration import NOW, FakeGateway, FakeRagService, _tool
from test_agent_archived_courses import _world

from aaos.agent.academic_orchestrator import ReadOnlyAcademicAssistant
from aaos.agent.conversation_context import (
    ContextEntityKind,
    ContextReferenceResolver,
    ContextResolutionStatus,
    ConversationContextSnapshot,
    ConversationContextStore,
    ConversationContextUpdate,
    TrustedContextEntity,
)
from aaos.rag.contracts import (
    CourseScope,
    GlobalScope,
    GroundedModelOutput,
    RagAnswerStatus,
    RagChunkSourceSpanValue,
    RagClaim,
    RetrievedEvidence,
)
from aaos.rag.generation import CitationValidator, render_rag_answer
from aaos.web.routes import create_api_v1_router

REPORTED = "What is KNIME? Explain it using only my currently available indexed lecture documents. Include citations if supporting documents are found."
COMPARISON = "Compare this with the previous lecture."


def test_comparison_without_context_is_missing() -> None:
    resolution = ContextReferenceResolver().resolve(
        COMPARISON, ConversationContextSnapshot(uuid4(), 0)
    )
    assert resolution.status is ContextResolutionStatus.MISSING
    assert resolution.entity is None


@pytest.mark.parametrize(
    "question",
    [REPORTED, "What is KNIME? Please explain it.", "What are decision trees? Describe it."],
)
def test_self_contained_explanation_is_not_a_prior_record_reference(question: str) -> None:
    snapshot = ConversationContextSnapshot(uuid4(), 0)
    resolution = ContextReferenceResolver().resolve(question, snapshot)
    assert resolution.status is ContextResolutionStatus.NONE
    assert resolution.entity is None


def test_local_topic_does_not_accidentally_inherit_an_unrelated_active_entity() -> None:
    entity = TrustedContextEntity(
        kind=ContextEntityKind.COURSE,
        entity_id=7,
        title="Deep Learning",
        course_id=None,
        course_title=None,
        source_tool="academic.list_courses",
        source_ref="read-1",
        source_turn=1,
    )
    snapshot = ConversationContextSnapshot(uuid4(), 1, entities=(entity,), active_entity=entity)
    assert (
        ContextReferenceResolver().resolve(REPORTED, snapshot).status
        is ContextResolutionStatus.NONE
    )
    assert ContextReferenceResolver().resolve("Explain it.", snapshot).entity == entity
    comparison = ContextReferenceResolver().resolve(COMPARISON, snapshot)
    assert comparison.status is ContextResolutionStatus.RESOLVED
    assert comparison.entity == entity


@pytest.mark.parametrize(
    "question",
    [
        "Explain it.",
        "Explain document 999.",
        "What is it? Explain it.",
        "What is that assignment? Explain it.",
        "What is the first document? Explain it.",
        "What is my assignment? Explain it.",
        "What is KNIME? Explain it and mark it complete.",
        "What is KNIME? Explain it and delete it please",
        "What is KNIME? Explain it and upload it please",
        "What is KNIME? Explain it and change its priority please",
        "What is KNIME? Explain it and احذف المهمة",
        "What is KNIME? Explain it and open document 999.",
    ],
)
def test_real_missing_references_still_fail_closed(question: str) -> None:
    result = ContextReferenceResolver().resolve(question, ConversationContextSnapshot(uuid4(), 0))
    assert result.status is ContextResolutionStatus.MISSING
    assert result.entity is None


def _validated_answer(version: int, course: int):
    text = "KNIME supports graphical data workflows."
    evidence = RetrievedEvidence(
        evidence_id="ev-1",
        chunk_key="fixture-chunk",
        exact_text=text,
        heading=None,
        document_version_id=version,
        document_title="Lecture.pdf",
        course_id=course,
        source_spans=(RagChunkSourceSpanValue(1, 1, 0, len(text), 0, len(text)),),
        dense_rank=1,
        lexical_rank=None,
        fused_rank=1,
        generation_id=4,
        content_revision=9,
        token_count=10,
    )
    answer = CitationValidator().validate(
        GroundedModelOutput(
            status=RagAnswerStatus.ANSWER,
            answer_text=text,
            language="en",
            claims=(RagClaim("claim-1", text, ("ev-1",)),),
            generation_id=4,
            content_revision=9,
            policy_version="grounded-v1",
        ),
        (evidence,),
    )
    return render_rag_answer(answer)


def _post(client: TestClient, endpoint: str, question: str, conversation_id=None):
    body = {"question": question}
    if conversation_id is not None:
        body["conversation_id"] = str(conversation_id)
    response = client.post(endpoint, json=body)
    assert response.status_code == 200
    if endpoint.endswith("/stream"):
        frames = response.text.split("\n\n")
        terminal = [frame for frame in frames if frame.startswith("event: result")]
        assert len(terminal) == 1
        assert all(
            "answer_text" not in frame for frame in frames if frame.startswith("event: stage")
        )
        return json.loads(terminal[0].split("data: ", 1)[1])
    return response.json()


def _client(*, past=False, store=None):
    academic, documents = _world(current_documents=not past)
    gateway = FakeGateway()
    version, course = (202, 5) if past else (101, 7)
    rag = FakeRagService(_validated_answer(version, course))
    assistant = ReadOnlyAcademicAssistant(
        gateway,
        academic,
        documents,
        rag,
        clock=lambda: NOW,
        intent_routing=True,
        context_store=store,
    )
    app = FastAPI()
    app.include_router(create_api_v1_router(academic, ask_service=assistant))
    return TestClient(app), rag, gateway


@pytest.mark.parametrize("endpoint", ["/api/v1/ask", "/api/v1/ask/stream"])
@pytest.mark.parametrize("past", [False, True])
def test_fresh_local_topic_reaches_verified_versioned_documents(endpoint: str, past: bool) -> None:
    client, rag, gateway = _client(past=past)
    question = (
        ("What is KNIME? Explain it using the archived SE440 lecture documents.")
        if past
        else REPORTED
    )
    with client:
        result = _post(client, endpoint, question)
    assert result["status"] == "ANSWERED"
    assert result["refusal_code"] is None
    assert result["rag_provenance"]["citations"][0]["document_version_id"] == (202 if past else 101)
    assert result["rag_provenance"]["from_past_course"] is past
    assert [query.scope for query in rag.queries] == [CourseScope(5) if past else GlobalScope()]
    assert gateway.requests == []


@pytest.mark.parametrize("endpoint", ["/api/v1/ask", "/api/v1/ask/stream"])
def test_simple_definition_keeps_existing_behavior(endpoint: str) -> None:
    client, rag, _gateway = _client()
    with client:
        result = _post(client, endpoint, "Define KNIME.")
    assert result["status"] == "ANSWERED"
    assert len(rag.queries) == 1


@pytest.mark.parametrize("endpoint", ["/api/v1/ask", "/api/v1/ask/stream"])
@pytest.mark.parametrize("question", ["Explain it.", "Explain document 999.", COMPARISON])
def test_fresh_invalid_reference_never_reaches_retrieval(endpoint: str, question: str) -> None:
    client, rag, gateway = _client()
    with client:
        result = _post(client, endpoint, question)
    assert result["status"] == "REFUSED"
    assert result["refusal_code"] == "UNRESOLVED_REFERENCE"
    assert rag.queries == [] and gateway.requests == []


@pytest.mark.parametrize("endpoint", ["/api/v1/ask", "/api/v1/ask/stream"])
def test_valid_prior_course_reference_still_uses_the_trusted_scope(endpoint: str) -> None:
    store = ConversationContextStore()
    lease = store.begin_turn()
    course = TrustedContextEntity(
        kind=ContextEntityKind.COURSE,
        entity_id=7,
        title="Deep Learning",
        course_id=None,
        course_title=None,
        source_tool="academic.list_courses",
        source_ref="read-1",
        source_turn=1,
    )
    store.finish_turn(
        lease,
        ConversationContextUpdate(entities=(course,), active_entity=course, active_course=course),
    )
    client, rag, gateway = _client(store=store)
    gateway.decisions.append(_tool("rag.answer", '{"course_name":null,"context_ref":"ctx-1"}'))
    with client:
        result = _post(
            client, endpoint, "Explain it using its lecture documents.", lease.conversation_id
        )
    assert result["status"] == "ANSWERED"
    assert [query.scope for query in rag.queries] == [CourseScope(7)]
