from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aaos.persistence.academic_models import Base, CourseORM, RemoteFileORM
from aaos.persistence.document_models import DocumentORM, DocumentVersionORM
from aaos.rag.contracts import (
    Citation,
    CourseScope,
    GlobalScope,
    RagAnswer,
    RagAnswerStatus,
    RagErrorCode,
    RagOperationalError,
    RagQuery,
    RenderedRagResponse,
)
from aaos.web.contracts import (
    RagCourseScopeRequest,
    RagGlobalScopeRequest,
    RagQueryRequest,
)
from aaos.web.rag import RagReadService


class FakeRagService:
    def __init__(self, response: RenderedRagResponse | Exception) -> None:
        self.response = response
        self.queries: list[RagQuery] = []

    async def ask(self, query: RagQuery) -> RenderedRagResponse:
        self.queries.append(query)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _validated_response(
    status: RagAnswerStatus = RagAnswerStatus.ANSWER,
    citations: tuple[Citation, ...] = (),
) -> RenderedRagResponse:
    answer = object.__new__(RagAnswer)
    object.__setattr__(answer, "status", status)
    object.__setattr__(answer, "answer_text", "Validated answer")
    object.__setattr__(answer, "language", "en")
    object.__setattr__(answer, "claims", ())
    object.__setattr__(answer, "citations", citations)
    object.__setattr__(answer, "generation_id", 1)
    object.__setattr__(answer, "content_revision", 1)
    from aaos.rag.contracts import _RAG_ANSWER_VALIDATION_MARKER

    object.__setattr__(answer, "_validation_marker", _RAG_ANSWER_VALIDATION_MARKER)
    return RenderedRagResponse(answer=answer, rendered_text="Validated answer")


def _sessions() -> sessionmaker:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(CourseORM(id=1, source_system="test", source_id="course-1", title="Course"))
        session.add(
            RemoteFileORM(
                id=1,
                course_id=1,
                source_system="test",
                source_id="file-1",
                title="Notes",
                is_active=True,
            )
        )
        session.add(
            DocumentORM(
                id=7,
                remote_file_id=1,
                ingestion_status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            DocumentVersionORM(
                id=11,
                document_id=7,
                version_number=1,
                content_blob_id=1,
                source_filename="notes.pdf",
                ingested_at=now,
                extraction_status="SUCCESS",
                page_count=3,
            )
        )
    return sessions


def test_rag_read_service_builds_trusted_global_and_course_queries() -> None:
    fake = FakeRagService(_validated_response(RagAnswerStatus.INSUFFICIENT_EVIDENCE))
    service = RagReadService(fake, _sessions())

    asyncio.run(
        service.ask(
            RagQueryRequest(
                question="Global question",
                scope=RagGlobalScopeRequest(type="global"),
            )
        )
    )
    asyncio.run(
        service.ask(
            RagQueryRequest(
                question="Course question",
                scope=RagCourseScopeRequest(type="course", course_id=1),
            )
        )
    )

    assert fake.queries[0] == RagQuery("Global question", GlobalScope())
    assert fake.queries[1] == RagQuery("Course question", CourseScope(1))


def test_insufficient_evidence_is_a_successful_empty_citation_projection() -> None:
    fake = FakeRagService(_validated_response(RagAnswerStatus.INSUFFICIENT_EVIDENCE))
    result = asyncio.run(
        RagReadService(fake, _sessions()).ask(
            RagQueryRequest(
                question="Question",
                scope=RagGlobalScopeRequest(type="global"),
            )
        )
    )

    assert result.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == ()


def test_citations_are_mapped_to_public_document_identity() -> None:
    fake = FakeRagService(
        _validated_response(
            citations=(
                Citation(
                    claim_id="c1",
                    document_version_id=11,
                    document_title="Untrusted model title",
                    page_start=2,
                    page_end=3,
                ),
            )
        )
    )

    result = asyncio.run(
        RagReadService(fake, _sessions()).ask(
            RagQueryRequest(
                question="Question",
                scope=RagGlobalScopeRequest(type="global"),
            )
        )
    )

    assert result.citations[0].document_id == 7
    assert result.citations[0].document_title == "Notes"
    assert result.citations[0].page_start == 2
    assert result.citations[0].page_end == 3


def test_operational_errors_are_not_rewritten_as_answers() -> None:
    fake = FakeRagService(RagOperationalError(RagErrorCode.MODEL_UNAVAILABLE))

    with pytest.raises(RagOperationalError):
        asyncio.run(
            RagReadService(fake, _sessions()).ask(
                RagQueryRequest(
                    question="Question",
                    scope=RagGlobalScopeRequest(type="global"),
                )
            )
        )
