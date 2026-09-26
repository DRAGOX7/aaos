from __future__ import annotations

from fixtures.phase7d_rag_acceptance import Phase7dFixtureManifest, fixture_answer

from aaos.rag.contracts import RagAnswerStatus


def test_frozen_fixture_covers_validated_answer_partial_and_insufficient_states() -> None:
    manifest = Phase7dFixtureManifest()
    answers = tuple(fixture_answer(status) for status in RagAnswerStatus)

    assert manifest.name == "phase7d-rag-fixture-v1"
    assert {answer.status.value for answer in answers} == {
        status.value for status in RagAnswerStatus
    }
    assert answers[2].citations == ()
    assert manifest.model_calls == 0
    assert manifest.writes == 0


def test_fixture_does_not_include_authority_or_internal_fields() -> None:
    answer = fixture_answer(RagAnswerStatus.ANSWER)
    serialized = str(answer.model_dump())

    for forbidden in (
        "document_version_id",
        "provider",
        "model",
        "permission",
        "approval",
        "storage_key",
        "traceback",
    ):
        assert forbidden not in serialized.lower()
