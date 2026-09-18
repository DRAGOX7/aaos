"""Trusted read-only composition between browser RAG and Phase 4."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.document_models import DocumentORM, DocumentVersionORM
from aaos.web.contracts import (
    RagAnswerResponse,
    RagAnswerStatusView,
    RagCitation,
    RagCourseScopeRequest,
    RagGlobalScopeRequest,
    RagQueryRequest,
)

if TYPE_CHECKING:
    from aaos.rag.contracts import (
        Citation,
        LocalRagService,
        RagQuery,
        RagScope,
    )


class AcademicRagReader(Protocol):
    """Narrow protocol consumed by the browser route."""

    async def ask(self, request: RagQueryRequest) -> RagAnswerResponse: ...


class RagReadService(AcademicRagReader):
    """Adapt the validated Phase 4 answer into a safe browser projection."""

    def __init__(self, rag_service: LocalRagService, sessions: sessionmaker[Session]) -> None:
        self._rag_service = rag_service
        self._sessions = sessions

    async def ask(self, request: RagQueryRequest) -> RagAnswerResponse:
        from aaos.rag.contracts import RagAnswerStatus, RagErrorCode, RagOperationalError

        query = self._build_query(request)
        response = await self._rag_service.ask(query)
        answer = response.answer
        if answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            citations: tuple[RagCitation, ...] = ()
        else:
            citations = self._project_citations(answer.citations)
        try:
            return RagAnswerResponse(
                status=RagAnswerStatusView(answer.status.value),
                answer_text=answer.answer_text,
                language=answer.language,
                citations=citations,
            )
        except (TypeError, ValueError) as error:
            raise RagOperationalError(RagErrorCode.INVALID_STRUCTURED_OUTPUT) from error

    @staticmethod
    def _build_query(request: RagQueryRequest) -> RagQuery:
        from aaos.rag.contracts import (
            CourseScope,
            GlobalScope,
            RagErrorCode,
            RagOperationalError,
            RagQuery,
        )

        scope = request.scope
        if isinstance(scope, RagGlobalScopeRequest):
            trusted_scope: RagScope = GlobalScope()
        elif isinstance(scope, RagCourseScopeRequest):
            trusted_scope = CourseScope(scope.course_id)
        else:
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        try:
            return RagQuery(question=request.question, scope=trusted_scope)
        except (TypeError, ValueError) as error:
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE) from error

    def _project_citations(self, citations: Iterable[Citation]) -> tuple[RagCitation, ...]:
        from aaos.rag.contracts import RagErrorCode, RagOperationalError

        citation_items = tuple(citations)
        if not citation_items:
            return ()
        version_ids: list[int] = []
        for citation in citation_items:
            version_id = citation.document_version_id
            if type(version_id) is not int or version_id <= 0:
                raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
            version_ids.append(version_id)

        statement = (
            select(DocumentVersionORM.id, DocumentORM.id, RemoteFileORM.title)
            .join(DocumentORM, DocumentORM.id == DocumentVersionORM.document_id)
            .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
            .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
            .where(
                DocumentVersionORM.id.in_(version_ids),
                DocumentORM.id > 0,
                RemoteFileORM.is_active.is_(True),
                CourseORM.is_active.is_(True),
            )
        )
        with self._sessions() as session:
            rows = session.execute(statement).all()
        targets = {int(row[0]): (int(row[1]), str(row[2])) for row in rows}
        if len(targets) != len(set(version_ids)):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

        result: list[RagCitation] = []
        for citation in citation_items:
            version_id = int(citation.document_version_id)
            target = targets.get(version_id)
            if target is None:
                raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
            try:
                result.append(
                    RagCitation(
                        document_id=target[0],
                        document_title=target[1],
                        page_start=citation.page_start,
                        page_end=citation.page_end,
                    )
                )
            except (TypeError, ValueError) as error:
                raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED) from error
        return tuple(result)
