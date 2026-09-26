from __future__ import annotations

from fastapi.testclient import TestClient

from aaos.main import create_app
from aaos.rag.contracts import RagErrorCode, RagOperationalError
from aaos.web.contracts import RagAnswerResponse, RagAnswerStatusView, RagQueryRequest


class FakeRagReader:
    def __init__(self, response: RagAnswerResponse | Exception) -> None:
        self.response = response
        self.requests: list[RagQueryRequest] = []

    async def ask(self, request: RagQueryRequest) -> RagAnswerResponse:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _answer(status: RagAnswerStatusView = RagAnswerStatusView.ANSWER) -> RagAnswerResponse:
    return RagAnswerResponse(
        status=status,
        answer_text="Validated answer",
        language="en",
        citations=(),
    )


def test_rag_query_returns_typed_validated_result() -> None:
    reader = FakeRagReader(_answer())
    client = TestClient(create_app(rag_reader=reader))

    response = client.post(
        "/api/v1/rag/queries",
        json={"question": "Question", "scope": {"type": "global"}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ANSWER",
        "answer_text": "Validated answer",
        "language": "en",
        "citations": [],
    }
    assert reader.requests[0].scope.type == "global"


def test_rag_query_operational_failure_is_sanitized() -> None:
    reader = FakeRagReader(RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE))
    client = TestClient(create_app(rag_reader=reader))

    response = client.post(
        "/api/v1/rag/queries",
        json={"question": "Question", "scope": {"type": "global"}},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RAG_UNAVAILABLE"
    assert "MODEL_UNAVAILABLE" not in response.text
    assert "rag operation failed" not in response.text


def test_rag_query_rejects_authority_fields_and_bad_scope() -> None:
    client = TestClient(create_app(rag_reader=FakeRagReader(_answer())))

    for payload in (
        {"question": "Question", "scope": {"type": "global"}, "provider": "x"},
        {"question": "Question", "scope": {"type": "course", "course_id": 0}},
        {"question": "Question", "scope": {"type": "historical", "document_version_id": 1}},
    ):
        response = client.post("/api/v1/rag/queries", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_openapi_exposes_only_browser_rag_contract() -> None:
    client = TestClient(create_app(rag_reader=FakeRagReader(_answer())))
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/rag/queries"]["post"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "provider" not in str(operation)
    assert "model" not in str(operation)
