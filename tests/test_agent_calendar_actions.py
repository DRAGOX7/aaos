from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from test_phase7e_task_service import ACTOR, APPROVER, _config, _definition

from aaos.agent.academic_orchestrator import (
    AcademicAskResult,
    AcademicAskStatus,
    ReadOnlyAcademicAssistant,
)
from aaos.agent.calendar_actions import (
    CalendarIntentError,
    build_calendar_event_plan,
    has_calendar_action_reference,
    has_explicit_calendar_create_intent,
)
from aaos.agent.contracts import PermissionClass
from aaos.agent.conversation_context import ConversationContextStore
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import FakeExternalAdapter
from aaos.approval.contracts import RemotePrecondition, TrustedApproverContext
from aaos.approval.repository import ApprovalRepository
from aaos.inference import ChatResponse, TokenUsage
from aaos.integrations.google_calendar.canonicalization import canonicalize_calendar_request
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    calendar_target,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.study_tasks.contracts import (
    StudyTask,
    StudyTaskPriority,
    StudyTaskStatus,
)
from aaos.tasks.contracts import BrowserTaskState, TaskKind
from aaos.tasks.execution import TaskExecutionCoordinator
from aaos.tasks.read_service import TaskReadService
from aaos.tasks.repository import TaskRepository
from aaos.tasks.service import TaskApplicationService, TaskApprovalService
from aaos.web.contracts import (
    ApprovalState,
    ApprovalSummary,
    AssignmentSummary,
    CourseSummary,
    DocumentSummary,
    SafeActionSummary,
    TaskDetail,
)
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router

NOW = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
CALENDAR_ZONE = "Asia/Amman"


class FakeGateway:
    def __init__(self, *decisions: str) -> None:
        self.decisions = list(decisions)
        self.requests: list[Any] = []

    async def check_availability(self) -> Any:
        raise AssertionError("availability is not part of an ask request")

    async def generate(self, request: Any) -> ChatResponse:
        self.requests.append(request)
        if not self.decisions:
            raise AssertionError("unexpected extra model decision")
        return ChatResponse(
            content=self.decisions.pop(0),
            model_identifier="calendar-agent-test",
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


class FakeAcademicReader:
    def __init__(self, *, now: datetime = NOW) -> None:
        self.assignments = (
            AssignmentSummary(
                id=20,
                course_id=7,
                title="Neural Networks Report",
                description=None,
                due_at=now + timedelta(days=3),
                updated_at=now,
            ),
            AssignmentSummary(
                id=22,
                course_id=8,
                title="SQL Project",
                description=None,
                due_at=now + timedelta(days=20),
                updated_at=now,
            ),
        )
        self.courses = (
            CourseSummary(
                id=7, title="Deep Learning", code="CS401", description=None, updated_at=now
            ),
            CourseSummary(id=8, title="Databases", code="CS301", description=None, updated_at=now),
        )
        self.calls: list[tuple[str, int | None]] = []

    def list_courses(self, *, limit: int | None = None) -> tuple[CourseSummary, ...]:
        return self.courses[:limit]

    def get_course(self, course_id: int) -> CourseSummary:
        return next(item for item in self.courses if item.id == course_id)

    def list_assignments(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[AssignmentSummary, ...]:
        self.calls.append(("list_assignments", course_id))
        rows = tuple(
            item for item in self.assignments if course_id is None or item.course_id == course_id
        )
        return rows[:limit]

    def get_assignment(self, assignment_id: int) -> AssignmentSummary:
        self.calls.append(("get_assignment", assignment_id))
        return next(item for item in self.assignments if item.id == assignment_id)


class FakeDocumentReader:
    def list_documents(
        self,
        course_id: int | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[DocumentSummary, ...]:
        del course_id, limit
        return ()

    def get_document(self, document_id: int) -> DocumentSummary:
        raise AssertionError(document_id)


class FakeRagService:
    async def ask(self, query: Any) -> Any:
        raise AssertionError(query)


class FakeStudyTaskService:
    def __init__(self) -> None:
        self.task = StudyTask(
            id=41,
            title="Review chapter 5",
            status=StudyTaskStatus.OPEN,
            priority=StudyTaskPriority.NORMAL,
            course_id=7,
            course_title="Deep Learning",
            assignment_id=20,
            assignment_title="Neural Networks Report",
            due_at=NOW + timedelta(days=3),
            created_at=NOW,
            updated_at=NOW,
        )

    def list(
        self,
        *,
        status: StudyTaskStatus | None = None,
        course_id: int | None = None,
        limit: int = 50,
    ) -> tuple[StudyTask, ...]:
        if status is StudyTaskStatus.COMPLETED or course_id not in (None, 7):
            return ()
        return (self.task,)[:limit]

    def get(self, task_id: int) -> StudyTask:
        if task_id != self.task.id:
            raise AssertionError(task_id)
        return self.task

    def create(self, **kwargs: Any) -> StudyTask:
        raise AssertionError(kwargs)

    def update(self, task_id: int, **kwargs: Any) -> StudyTask:
        raise AssertionError((task_id, kwargs))

    def complete(self, task_id: int, *, request_id: UUID) -> StudyTask:
        raise AssertionError((task_id, request_id))


class FakeCalendarProposalService:
    def __init__(self) -> None:
        self.calls: list[tuple[CalendarCreateRequest, str, str, datetime]] = []
        self.records: dict[str, TaskDetail] = {}
        self.scopes: dict[str, str] = {}
        self.provider_write_count = 0

    def create_calendar_task(
        self,
        request: CalendarCreateRequest,
        *,
        idempotency_key: str,
        idempotency_scope: str,
        now: datetime,
    ) -> TaskDetail:
        self.calls.append((request, idempotency_key, idempotency_scope, now))
        previous = self.records.get(idempotency_key)
        if previous is not None:
            if self.scopes[idempotency_key] != idempotency_scope:
                raise ValueError("idempotency scope conflicts with existing request")
            return previous
        zone = ZoneInfo(request.timezone)
        start = (
            request.start.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        end = (
            request.end.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        action = SafeActionSummary(
            action_type="CREATE_EVENT",
            integration_label="Google Calendar",
            summary=request.summary,
            start=start,
            end=end,
            timezone=zone.key,
            description=request.description,
            location=request.location,
        )
        approval = ApprovalSummary(
            approval_id="approval-opaque-123",
            task_id="proposal-123",
            action=action,
            status=ApprovalState.PENDING,
            issued_at=now,
            approval_expires_at=now + timedelta(hours=1),
            operation_state=None,
            can_approve=True,
            can_reject=True,
        )
        detail = TaskDetail(
            task_id="proposal-123",
            task_kind=TaskKind.CALENDAR_CREATE_EVENT,
            status=BrowserTaskState.AWAITING_APPROVAL,
            created_at=now,
            updated_at=now,
            action=action,
            approval=approval,
            operation_state=None,
            operation_outcome=None,
            action_expires_at=now + timedelta(hours=2),
            failure_code=None,
        )
        self.records[idempotency_key] = detail
        self.scopes[idempotency_key] = idempotency_scope
        return detail


def _tool(name: str, arguments: dict[str, object] | None = None) -> str:
    return json.dumps({"action": "CALL_TOOL", "tool_name": name, "arguments": arguments or {}})


def _answer(reference: str) -> str:
    return json.dumps({"action": "ANSWER", "evidence_refs": [reference]})


def _refuse() -> str:
    return json.dumps({"action": "REFUSE", "reason": "UNSUPPORTED_CAPABILITY"})


def _assistant(
    gateway: FakeGateway,
    *,
    calendar: FakeCalendarProposalService,
    academic: FakeAcademicReader | None = None,
    study_tasks: FakeStudyTaskService | None = None,
    calendar_timezone: str | None = CALENDAR_ZONE,
    context_store: ConversationContextStore | None = None,
) -> tuple[ReadOnlyAcademicAssistant, FakeAcademicReader]:
    resolved_academic = academic or FakeAcademicReader()
    assistant = ReadOnlyAcademicAssistant(
        gateway,
        resolved_academic,
        FakeDocumentReader(),
        FakeRagService(),
        clock=lambda: NOW,
        context_store=context_store,
        study_task_service=study_tasks,
        calendar_task_service=calendar,
        calendar_timezone=calendar_timezone,
    )
    return assistant, resolved_academic


def test_second_assignment_deadline_uses_phase_b_context_and_stays_pending() -> None:
    gateway = FakeGateway(
        _tool("academic.list_assignments"),
        _answer("read-1"),
        _tool("calendar.propose_create_event", {"context_ref": "ctx-1"}),
        _refuse(),
    )
    calendar = FakeCalendarProposalService()
    assistant, academic = _assistant(gateway, calendar=calendar)

    listed = asyncio.run(assistant.ask("What assignments do I have this week?"))
    result = asyncio.run(
        assistant.ask(
            "Put the second one on my calendar.",
            conversation_id=listed.conversation_id,
            request_id=UUID("10000000-0000-4000-8000-000000000101"),
        )
    )

    assert listed.status is AcademicAskStatus.ANSWERED
    assert result.status.value == "CALENDAR_PROPOSAL_CREATED"
    assert result.calendar_proposal is not None
    assert result.calendar_proposal.source_entity_kind == "assignment"
    assert result.calendar_proposal.source_entity_id == 22
    assert result.calendar_proposal.summary == "Assignment deadline: SQL Project"
    assert result.calendar_proposal.approval_status == "PENDING"
    assert result.calendar_proposal.approval_id == "approval-opaque-123"
    assert "not been created" in (result.answer_text or "").casefold()
    assert len(calendar.calls) == 1
    request, key, scope, _now = calendar.calls[0]
    assert key == "calendar-10000000-0000-4000-8000-000000000101"
    assert scope == "assignment-22"
    assert request.start == datetime(2026, 10, 17, 12, tzinfo=ZoneInfo(CALENDAR_ZONE))
    assert request.end - request.start == timedelta(minutes=15)
    assert calendar.provider_write_count == 0
    assert ("get_assignment", 22) in academic.calls
    assert gateway.requests[2].structured_output_schema == ("calendar_proposal_decision_contextual")
    serialized_requests = "\n".join(
        message.content for sent in gateway.requests for message in sent.messages
    )
    assert "approval-opaque-123" not in serialized_requests


def test_study_session_uses_trusted_task_and_python_time_parser() -> None:
    gateway = FakeGateway(
        _tool("study_task.list", {"status": "OPEN"}),
        _answer("read-1"),
        _tool("calendar.propose_create_event", {"context_ref": "ctx-1"}),
        _refuse(),
    )
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(
        gateway,
        calendar=calendar,
        study_tasks=FakeStudyTaskService(),
    )

    listed = asyncio.run(assistant.ask("Show my unfinished tasks"))
    result = asyncio.run(
        assistant.ask(
            "Schedule two hours for the first one tomorrow at 6 PM.",
            conversation_id=listed.conversation_id,
            request_id=UUID("10000000-0000-4000-8000-000000000102"),
        )
    )

    assert result.status.value == "CALENDAR_PROPOSAL_CREATED"
    assert result.calendar_proposal is not None
    assert result.calendar_proposal.source_entity_kind == "study_task"
    assert result.calendar_proposal.source_entity_id == 41
    assert result.calendar_proposal.summary == "Study session: Review chapter 5"
    request, _key, _scope, _now = calendar.calls[0]
    assert request.start == datetime(2026, 9, 28, 18, tzinfo=ZoneInfo(CALENDAR_ZONE))
    assert request.end - request.start == timedelta(hours=2)
    assert request.timezone == CALENDAR_ZONE
    assert calendar.provider_write_count == 0


def test_assignment_can_be_resolved_from_current_turn_trusted_read_evidence() -> None:
    gateway = FakeGateway(
        _tool(
            "academic.get_assignment",
            {"assignment_title": "Neural Networks Report", "course_name": "Deep Learning"},
        ),
        _tool("calendar.propose_create_event", {"source_ref": "read-1"}),
        _refuse(),
    )
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(gateway, calendar=calendar)

    result = asyncio.run(
        assistant.ask(
            "Put the Neural Networks Report deadline on my calendar.",
            request_id=UUID("10000000-0000-4000-8000-000000000103"),
        )
    )

    assert result.status.value == "CALENDAR_PROPOSAL_CREATED"
    assert result.calendar_proposal is not None
    assert result.calendar_proposal.source_entity_id == 20
    assert len(calendar.calls) == 1


def test_calendar_idempotency_key_is_bound_to_the_trusted_source_entity() -> None:
    gateway = FakeGateway(
        _tool(
            "academic.get_assignment",
            {"assignment_title": "Neural Networks Report", "course_name": "Deep Learning"},
        ),
        _tool("calendar.propose_create_event", {"source_ref": "read-1"}),
        _refuse(),
        _tool(
            "academic.get_assignment",
            {"assignment_title": "SQL Project", "course_name": "Databases"},
        ),
        _tool("calendar.propose_create_event", {"source_ref": "read-1"}),
        _refuse(),
    )
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(gateway, calendar=calendar)
    request_id = UUID("10000000-0000-4000-8000-000000000113")

    first = asyncio.run(
        assistant.ask(
            "Put the Neural Networks Report deadline on my calendar.",
            request_id=request_id,
        )
    )
    second = asyncio.run(
        assistant.ask(
            "Put the SQL Project deadline on my calendar.",
            request_id=request_id,
        )
    )

    assert first.calendar_proposal is not None and first.calendar_proposal.source_entity_id == 20
    assert second.status is AcademicAskStatus.FAILED
    assert [call[1] for call in calendar.calls] == [
        "calendar-10000000-0000-4000-8000-000000000113",
        "calendar-10000000-0000-4000-8000-000000000113",
    ]
    assert [call[2] for call in calendar.calls] == ["assignment-20", "assignment-22"]
    assert len(calendar.records) == 1


def test_one_ask_cannot_create_multiple_calendar_proposals() -> None:
    gateway = FakeGateway(
        _tool("academic.list_assignments"),
        _tool(
            "calendar.propose_create_event",
            {"source_ref": "read-1", "source_index": 1},
        ),
        _tool(
            "calendar.propose_create_event",
            {"source_ref": "read-1", "source_index": 2},
        ),
        _refuse(),
    )
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(gateway, calendar=calendar)

    result = asyncio.run(
        assistant.ask(
            "List assignments and put both deadlines on my calendar.",
            request_id=UUID("10000000-0000-4000-8000-000000000114"),
        )
    )

    assert result.status is AcademicAskStatus.CALENDAR_PROPOSAL_CREATED
    assert result.calendar_proposal is not None
    assert result.calendar_proposal.source_entity_id == 20
    assert len(calendar.calls) == 1
    assert len(calendar.records) == 1


def test_ambiguous_time_and_model_supplied_calendar_fields_fail_closed() -> None:
    ambiguous_gateway = FakeGateway(
        _tool("study_task.list", {"status": "OPEN"}),
        _answer("read-1"),
        _tool("calendar.propose_create_event", {"context_ref": "ctx-1"}),
        _refuse(),
    )
    ambiguous_calendar = FakeCalendarProposalService()
    ambiguous_assistant, _ = _assistant(
        ambiguous_gateway,
        calendar=ambiguous_calendar,
        study_tasks=FakeStudyTaskService(),
    )
    listed = asyncio.run(ambiguous_assistant.ask("Show my unfinished tasks"))
    ambiguous = asyncio.run(
        ambiguous_assistant.ask(
            "Schedule it Friday evening.",
            conversation_id=listed.conversation_id,
            request_id=UUID("10000000-0000-4000-8000-000000000104"),
        )
    )
    assert ambiguous_calendar.calls == []
    assert ambiguous.status is AcademicAskStatus.FAILED

    forged_gateway = FakeGateway(
        _tool("study_task.list", {"status": "OPEN"}),
        _answer("read-1"),
        _tool(
            "calendar.propose_create_event",
            {"context_ref": "ctx-1", "summary": "Forged", "start": "2099-01-01"},
        ),
    )
    forged_calendar = FakeCalendarProposalService()
    forged_assistant, _ = _assistant(
        forged_gateway,
        calendar=forged_calendar,
        study_tasks=FakeStudyTaskService(),
    )
    listed = asyncio.run(forged_assistant.ask("Show my unfinished tasks"))
    forged = asyncio.run(
        forged_assistant.ask(
            "Schedule a study session for the first one tomorrow at 6 PM.",
            conversation_id=listed.conversation_id,
            request_id=UUID("10000000-0000-4000-8000-000000000105"),
        )
    )
    assert forged_calendar.calls == []
    assert forged.status is AcademicAskStatus.FAILED


def test_calendar_capability_is_absent_without_request_id_or_trusted_timezone() -> None:
    for index, (request_id, timezone_name) in enumerate(
        ((None, CALENDAR_ZONE), (UUID("10000000-0000-4000-8000-000000000106"), None)),
        start=106,
    ):
        gateway = FakeGateway(
            _tool("study_task.list", {"status": "OPEN"}),
            _answer("read-1"),
            _refuse(),
        )
        calendar = FakeCalendarProposalService()
        assistant, _ = _assistant(
            gateway,
            calendar=calendar,
            study_tasks=FakeStudyTaskService(),
            calendar_timezone=timezone_name,
        )
        listed = asyncio.run(assistant.ask("Show my unfinished tasks"))
        result = asyncio.run(
            assistant.ask(
                "Schedule one hour for the first one tomorrow at 6 PM.",
                conversation_id=listed.conversation_id,
                request_id=request_id,
            )
        )
        assert calendar.calls == []
        assert result.status is AcademicAskStatus.REFUSED
        assert index in {106, 107}


def test_missing_assignment_due_date_does_not_create_a_calendar_proposal() -> None:
    academic = FakeAcademicReader()
    academic.assignments = (
        academic.assignments[0].model_copy(update={"due_at": None}),
        academic.assignments[1],
    )
    gateway = FakeGateway(
        _tool(
            "academic.get_assignment",
            {"assignment_title": "Neural Networks Report", "course_name": "Deep Learning"},
        ),
        _tool("calendar.propose_create_event", {"source_ref": "read-1"}),
    )
    calendar = FakeCalendarProposalService()
    assistant, _ = _assistant(gateway, calendar=calendar, academic=academic)

    result = asyncio.run(
        assistant.ask(
            "Put the Neural Networks Report deadline on my calendar.",
            request_id=UUID("10000000-0000-4000-8000-000000000109"),
        )
    )

    assert result.status is AcademicAskStatus.FAILED
    assert calendar.calls == []


def test_assignment_date_request_cannot_override_the_canonical_due_time() -> None:
    gateway = FakeGateway(
        _tool(
            "academic.get_assignment",
            {"assignment_title": "Neural Networks Report", "course_name": "Deep Learning"},
        ),
        _tool("calendar.propose_create_event", {"source_ref": "read-1"}),
    )
    calendar = FakeCalendarProposalService()
    assistant, _ = _assistant(gateway, calendar=calendar)

    result = asyncio.run(
        assistant.ask(
            "Put the Neural Networks Report deadline on my calendar tomorrow at 6 PM.",
            request_id=UUID("10000000-0000-4000-8000-000000000110"),
        )
    )

    assert result.status is AcademicAskStatus.FAILED
    assert calendar.calls == []


def test_calendar_update_and_delete_intents_are_not_supported() -> None:
    for question in (
        "Move that study session to 7 PM.",
        "Cancel that calendar event.",
        "Delete the calendar event.",
        "Move it to 7 PM.",
    ):
        assert not has_explicit_calendar_create_intent(question, source_entity_kind="study_task")
    assert has_calendar_action_reference("Move that study session to 7 PM.")
    assert has_calendar_action_reference("Move it to 7 PM.", source_entity_kind="study_task")
    assert has_calendar_action_reference("Cancel it.")


def test_negated_create_language_is_not_a_calendar_create_intent() -> None:
    for question in (
        "Don't put this deadline on my calendar.",
        "Don't ever add this deadline to my calendar.",
        "Do not create a calendar event for this assignment.",
    ):
        assert not has_explicit_calendar_create_intent(question)
        assert has_calendar_action_reference(question)
    assert has_calendar_action_reference("Don't schedule it.")


def test_conflicting_time_and_explicit_timezone_fail_closed() -> None:
    task = FakeStudyTaskService().task
    for question in (
        "Schedule it tomorrow at 6 PM, on 2026-10-01 at 7 PM.",
        "Schedule it tomorrow at 6 PM and Friday at 7 PM.",
        "Schedule it tomorrow at 6 PM or 7 PM.",
        "Schedule it tomorrow at 6 PM Europe/Paris.",
        "Schedule it tomorrow at 6 PM europe/paris.",
        "Schedule it tomorrow at 6 PM est.",
        "Schedule it tomorrow at 6 PM +03:00.",
        "Schedule it tomorrow at 6 PM UTC-0400.",
        "Schedule it tomorrow at 6 PM for two hours and one hour.",
        "Schedule it tomorrow at 6 PM for one or two hours.",
        "Schedule it tomorrow at 6 PM for 120 minutes.",
        "Schedule it tomorrow at 6 PM for 1.5 hours.",
    ):
        with pytest.raises(CalendarIntentError):
            build_calendar_event_plan(
                question,
                assignment=None,
                study_task=task,
                timezone_name=CALENDAR_ZONE,
                now=NOW,
            )


def test_event_summary_matches_calendar_nfkc_canonicalization() -> None:
    assignment = AssignmentSummary(
        id=23,
        course_id=7,
        title="Ｄｅｅｐ ﬁnal",
        description=None,
        due_at=NOW + timedelta(days=3),
        updated_at=NOW,
    )
    plan = build_calendar_event_plan(
        "Put this deadline on my calendar.",
        assignment=assignment,
        study_task=None,
        timezone_name=CALENDAR_ZONE,
        now=NOW,
    )

    canonical = canonicalize_calendar_request(plan.request, now=NOW)

    assert plan.request.summary == "Assignment deadline: Deep final"
    assert canonical.summary == plan.request.summary


def test_unsupported_calendar_action_does_not_offer_study_task_write() -> None:
    gateway = FakeGateway(
        _tool(
            "study_task.update",
            {"task_title": "Review chapter 5", "priority": "HIGH"},
        )
    )
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(
        gateway,
        calendar=calendar,
        study_tasks=FakeStudyTaskService(),
    )

    result = asyncio.run(
        assistant.ask(
            "Move the study session Review chapter 5 to 7 PM.",
            request_id=UUID("10000000-0000-4000-8000-000000000111"),
        )
    )

    assert result.status is AcademicAskStatus.FAILED
    assert calendar.calls == []
    assert calendar.provider_write_count == 0


def test_negated_calendar_request_does_not_offer_create_tool() -> None:
    gateway = FakeGateway(_tool("calendar.propose_create_event", {"source_ref": "read-1"}))
    calendar = FakeCalendarProposalService()
    assistant, _academic = _assistant(gateway, calendar=calendar)

    result = asyncio.run(
        assistant.ask(
            "Don't put this deadline on my calendar.",
            request_id=UUID("10000000-0000-4000-8000-000000000112"),
        )
    )

    assert result.status is AcademicAskStatus.FAILED
    assert calendar.calls == []


def test_approved_calendar_proposal_executes_once_through_existing_approval_path(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "phase-d-calendar.db")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = create_session_factory(engine)
    repositories = TaskRepository(sessions), ApprovalRepository(sessions)
    task_repository, approval_repository = repositories
    reader = TaskReadService(sessions)
    definition = _definition()
    permissions = PermissionPolicy.from_values({PermissionClass.LOCAL_STATE_WRITE})
    config = _config()
    proposal_service = TaskApplicationService(
        task_repository,
        approval_repository,
        definition=definition,
        capability_policy=permissions,
        actor_identity=ACTOR,
        approver=APPROVER,
        calendar_config=config,
    )
    now = datetime.now(timezone.utc)
    request_id = UUID("10000000-0000-4000-8000-000000000108")
    gateway = FakeGateway(
        _tool("academic.list_assignments"),
        _answer("read-1"),
        _tool("calendar.propose_create_event", {"context_ref": "ctx-1"}),
        _refuse(),
    )
    assistant = ReadOnlyAcademicAssistant(
        gateway,
        FakeAcademicReader(now=now),
        FakeDocumentReader(),
        FakeRagService(),
        clock=lambda: now,
        calendar_task_service=proposal_service,
        calendar_timezone=CALENDAR_ZONE,
    )
    try:
        listed = asyncio.run(assistant.ask("List my assignments."))
        proposed = asyncio.run(
            assistant.ask(
                "Create a calendar event for the second assignment.",
                conversation_id=listed.conversation_id,
                request_id=request_id,
            )
        )

        assert proposed.status is AcademicAskStatus.CALENDAR_PROPOSAL_CREATED
        assert proposed.calendar_proposal is not None
        assert proposed.calendar_proposal.approval_status == "PENDING"
        proposal_view = proposed.calendar_proposal
        canonical_request = CalendarCreateRequest(
            summary=proposal_view.summary,
            start=datetime.fromisoformat(proposal_view.start.replace("Z", "+00:00")).astimezone(
                ZoneInfo(proposal_view.timezone)
            ),
            end=datetime.fromisoformat(proposal_view.end.replace("Z", "+00:00")).astimezone(
                ZoneInfo(proposal_view.timezone)
            ),
            timezone=proposal_view.timezone,
        )
        idempotency_key = f"calendar-{request_id}"
        replayed = proposal_service.create_calendar_task(
            canonical_request,
            idempotency_key=idempotency_key,
            idempotency_scope=(
                f"{proposal_view.source_entity_kind}-{proposal_view.source_entity_id}"
            ),
            now=now,
        )
        assert replayed.task_id == proposal_view.proposal_id
        with pytest.raises(ValueError, match="idempotency key conflicts"):
            proposal_service.create_calendar_task(
                canonical_request,
                idempotency_key=idempotency_key,
                idempotency_scope="assignment-20",
                now=now,
            )
        adapter_target = calendar_target(config)
        provider = FakeExternalAdapter(
            RemotePrecondition(
                "google-calendar",
                adapter_target.resource,
                config.provider_adapter_version,
                "absent",
            )
        )
        executor = TaskExecutionCoordinator(
            task_repository,
            approval_repository,
            reader,
            adapter=provider,
            definition=definition,
            capability_policy=permissions,
        )
        commands = TaskApprovalService(approval_repository, reader)

        class FailBeforeClaimOnce:
            def __init__(self) -> None:
                self.calls = 0

            def execute_approved_task(self, task_id: str, *, now: datetime) -> TaskDetail:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("simulated interruption after approval commit")
                return executor.execute_approved_task(task_id, now=now)

        retryable_executor = FailBeforeClaimOnce()

        class Principal:
            def current(self) -> TrustedApproverContext:
                return APPROVER

        class AskReply:
            async def ask(
                self,
                question: str,
                cancel_event: asyncio.Event | None = None,
                *,
                conversation_id: UUID | None = None,
                request_id: UUID | None = None,
            ) -> AcademicAskResult:
                del question, cancel_event, conversation_id, request_id
                return proposed

        app = FastAPI()
        install_error_boundary(app)
        app.include_router(
            create_api_v1_router(
                FakeAcademicReader(),
                task_reader=reader,
                task_commands=commands,
                principal_provider=Principal(),
                ask_service=AskReply(),
                calendar_task_executor=retryable_executor,
            )
        )
        headers = {
            "host": "127.0.0.1:8000",
            "origin": "http://127.0.0.1:3000",
            "content-type": "application/json",
        }
        with TestClient(app, raise_server_exceptions=False) as client:
            assert provider.side_effect_count == 0
            ask_response = client.post(
                "/api/v1/ask",
                headers=headers,
                json={
                    "question": "Create a calendar event for the second assignment.",
                    "request_id": str(request_id),
                },
            )
            assert ask_response.status_code == 200
            ask_body = ask_response.json()
            assert ask_body["status"] == "CALENDAR_PROPOSAL_CREATED"
            assert ask_body["calendar_proposal"]["approval_status"] == "PENDING"
            assert ask_body["calendar_proposal"]["operation"] == "CREATE_EVENT"
            assert "not been created" in ask_body["answer_text"]
            assert provider.side_effect_count == 0

            interrupted = client.post(
                f"/api/v1/approvals/{proposed.calendar_proposal.approval_id}/approve",
                headers=headers,
                json={},
            )
            assert interrupted.status_code == 500
            assert provider.side_effect_count == 0
            assert reader.is_approved_and_unclaimed_by(
                proposed.calendar_proposal.approval_id,
                approver=APPROVER,
            )
            assert not reader.is_approved_and_unclaimed_by(
                proposed.calendar_proposal.approval_id,
                approver=TrustedApproverContext("different-user", "different-context"),
            )

            approved = client.post(
                f"/api/v1/approvals/{proposed.calendar_proposal.approval_id}/approve",
                headers=headers,
                json={},
            )
            assert approved.status_code == 200
            assert approved.json()["operation_state"] == "CONFIRMED_SUCCESS"
            assert retryable_executor.calls == 2
            assert provider.execute_count == 1
            assert provider.side_effect_count == 1

            replay = client.post(
                f"/api/v1/approvals/{proposed.calendar_proposal.approval_id}/approve",
                headers=headers,
                json={},
            )
            assert replay.status_code == 409, replay.text
            assert provider.execute_count == 1
            assert provider.side_effect_count == 1
    finally:
        engine.dispose()
