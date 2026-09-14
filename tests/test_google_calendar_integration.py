from __future__ import annotations

from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import JSONValue, PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import (
    DispatchResult,
    dispatch_claimed_operation,
    load_executable_action,
)
from aaos.approval.contracts import (
    ActionId,
    ActionType,
    ActorIdentity,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    AdapterReconciliationOutcome,
    ClaimIdentity,
    ExecutableAction,
    OperationId,
    OperationState,
    Phase6ActionDefinition,
    RemotePrecondition,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository
from aaos.approval.service import (
    approve_approval,
    claim_action,
    prepare_action,
    reconcile_operation,
    recover_inflight_operation,
    request_approval,
)
from aaos.integrations.google_calendar.adapter import GoogleCalendarAdapter
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.client import (
    FakeGoogleCalendarClient,
    ObservedGoogleEvent,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
    calendar_target,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import OperationRecordORM
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)

NOW = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DERIVATION_VERSION = "calendar-provider-id-v1"
APPROVER = TrustedApproverContext(
    "phase6b-reviewer",
    "trusted-review-session",
)


def config(
    *,
    calendar_id: str = "phase6b-test-calendar",
) -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id=calendar_id,
        expected_service_identity="phase6b-test-service",
        oauth_scope=EVENTS_SCOPE,
        credential_path="C:/trusted/not-opened.json",
        provider_adapter_version="calendar-adapter-v1",
        enabled=True,
    )


def make_calendar_adapter(
    repo: ApprovalRepository,
    client: FakeGoogleCalendarClient,
    *,
    calendar_config: GoogleCalendarConfig | None = None,
) -> GoogleCalendarAdapter:
    selected_config = calendar_config or config()

    def load_expected(operation_id: OperationId) -> ExecutableAction:
        return load_executable_action(repo, operation_id)

    return GoogleCalendarAdapter(
        client,
        selected_config,
        derivation_version=DERIVATION_VERSION,
        expected_action_loader=load_expected,
    )


def event() -> CanonicalCalendarEvent:
    local_now = NOW.astimezone(ZoneInfo("UTC"))

    return canonicalize_calendar_request(
        CalendarCreateRequest(
            summary="Architecture review",
            start=local_now + timedelta(hours=2),
            end=local_now + timedelta(hours=3),
            timezone="UTC",
            description="Review bounded integration",
            location="Room 6B",
        ),
        now=NOW,
    )


def definition() -> Phase6ActionDefinition:
    def validate(
        arguments: Mapping[str, JSONValue],
    ) -> Mapping[str, JSONValue]:
        required = {
            "summary",
            "start",
            "end",
            "timezone",
            "description",
            "location",
        }

        if set(arguments) != required:
            raise ValueError("calendar arguments do not match the closed schema")

        summary = arguments["summary"]
        start = arguments["start"]
        end = arguments["end"]
        timezone_name = arguments["timezone"]
        description = arguments["description"]
        location = arguments["location"]

        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
        if not isinstance(start, str):
            raise TypeError("start must be a string")
        if not isinstance(end, str):
            raise TypeError("end must be a string")
        if not isinstance(timezone_name, str):
            raise TypeError("required calendar arguments must be strings")
        if description is not None and not isinstance(description, str):
            raise TypeError("description must be a string or null")
        if location is not None and not isinstance(location, str):
            raise TypeError("location must be a string or null")

        canonical = CanonicalCalendarEvent._create(
            summary,
            start,
            end,
            timezone_name,
            description,
            location,
        )

        return canonical_event_arguments(canonical)

    return Phase6ActionDefinition(
        action_type=ActionType("CREATE_EVENT"),
        tool_name="calendar_create_event",
        tool_schema_version="phase6b-calendar-v1",
        tool_definition_fingerprint="6" * 64,
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version="phase5-v1",
        policy_fingerprint="7" * 64,
        timeout_seconds=10,
        argument_validator=validate,
    )


def policy(
    *permissions: PermissionClass,
) -> PermissionPolicy:
    return PermissionPolicy.from_values(permissions)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase6b-integration.db")

    Base.metadata.create_all(db_engine)

    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def sessions(
    engine: Engine,
) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def repo(
    sessions: sessionmaker[Session],
) -> ApprovalRepository:
    return ApprovalRepository(sessions)


def prepare_calendar(
    repo: ApprovalRepository,
    calendar_config: GoogleCalendarConfig,
) -> ActionId:
    target = calendar_target(calendar_config)

    prepared = prepare_action(
        repo,
        definition(),
        actor_identity=ActorIdentity("phase6b-user"),
        target_identity=target,
        arguments=canonical_event_arguments(event()),
        remote_precondition=RemotePrecondition(
            "google-calendar",
            target.resource,
            DERIVATION_VERSION,
            "absent",
        ),
        action_expires_at=NOW + timedelta(minutes=10),
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        now=NOW,
    )

    return prepared.action_id


def claim_calendar(
    repo: ApprovalRepository,
    calendar_config: GoogleCalendarConfig,
) -> OperationId:
    action_id = prepare_calendar(repo, calendar_config)

    requested = request_approval(
        repo,
        action_id,
        APPROVER,
        NOW + timedelta(minutes=5),
        NOW + timedelta(seconds=1),
    )

    approve_approval(
        repo,
        requested.approval_id,
        APPROVER,
        NOW + timedelta(seconds=2),
    )

    claimed = claim_action(
        repo,
        action_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=definition(),
        now=NOW + timedelta(seconds=3),
        claim_identity=ClaimIdentity("phase6b-worker"),
    )

    assert claimed.claimed
    assert claimed.operation_id is not None

    return claimed.operation_id


def dispatch(
    repo: ApprovalRepository,
    calendar_adapter: GoogleCalendarAdapter,
    operation_id: OperationId,
    *,
    capability_policy: PermissionPolicy | None = None,
) -> DispatchResult:
    return dispatch_claimed_operation(
        repo,
        calendar_adapter,
        operation_id,
        capability_policy=capability_policy or policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=definition(),
        now=NOW + timedelta(seconds=4),
    )


def test_durable_executing_precedes_provider_write(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
) -> None:
    operation_id = claim_calendar(repo, config())

    class OrderingClient(FakeGoogleCalendarClient):
        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            with sessions() as session:
                operation = session.get(
                    OperationRecordORM,
                    operation_id.value,
                )

                assert operation is not None
                assert operation.state == OperationState.EXECUTING.value
                assert operation.dispatch_started_at is not None

            return super().insert_event(
                calendar_id,
                event,
                provider_event_id,
            )

    client = OrderingClient()

    result = dispatch(
        repo,
        make_calendar_adapter(repo, client),
        operation_id,
    )

    assert result.adapter_invoked is True
    assert result.dispatch_started is True
    assert result.adapter_outcome is not None
    assert result.adapter_outcome.kind is AdapterOutcomeKind.SUCCESS


def test_two_workers_can_insert_at_most_once(
    repo: ApprovalRepository,
) -> None:
    operation_id = claim_calendar(repo, config())
    client = FakeGoogleCalendarClient()

    adapter_instance = make_calendar_adapter(
        repo,
        client,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: dispatch(
                    repo,
                    adapter_instance,
                    operation_id,
                ),
                range(2),
            )
        )

    assert [result.adapter_invoked for result in results].count(True) == 1
    assert client.insert_count == 1
    assert len(client.events) == 1


def test_adapter_receives_only_least_authority_projection(
    repo: ApprovalRepository,
) -> None:
    operation_id = claim_calendar(repo, config())
    client = FakeGoogleCalendarClient()

    delegate = make_calendar_adapter(
        repo,
        client,
    )

    seen: list[ExecutableAction] = []

    class RecordingAdapter:
        def execute(
            self,
            executable: ExecutableAction,
        ) -> AdapterExecutionOutcome:
            seen.append(executable)
            return delegate.execute(executable)

        def reconcile(
            self,
            selected_operation_id: OperationId,
        ) -> AdapterReconciliationOutcome:
            return delegate.reconcile(selected_operation_id)

    result = dispatch_claimed_operation(
        repo,
        RecordingAdapter(),
        operation_id,
        capability_policy=policy(PermissionClass.LOCAL_STATE_WRITE),
        definition=definition(),
        now=NOW + timedelta(seconds=4),
    )

    assert result.adapter_invoked is True
    assert len(seen) == 1

    assert set(seen[0].__dataclass_fields__) == {
        "target_identity",
        "action_type",
        "canonical_arguments",
        "operation_id",
        "remote_precondition",
    }


def test_consumed_approval_and_capability_are_required(
    repo: ApprovalRepository,
) -> None:
    unclaimed_action_id = prepare_calendar(
        repo,
        config(),
    )

    client = FakeGoogleCalendarClient()

    adapter_instance = make_calendar_adapter(
        repo,
        client,
    )

    unclaimed = dispatch(
        repo,
        adapter_instance,
        OperationId("not-claimed"),
    )

    claimed_operation = claim_calendar(
        repo,
        config(),
    )

    denied = dispatch(
        repo,
        adapter_instance,
        claimed_operation,
        capability_policy=policy(PermissionClass.READ_ONLY),
    )

    assert isinstance(unclaimed_action_id, ActionId)
    assert unclaimed.adapter_invoked is False
    assert denied.adapter_invoked is False
    assert client.insert_count == 0


def test_config_or_target_drift_fails_before_write(
    repo: ApprovalRepository,
) -> None:
    operation_id = claim_calendar(
        repo,
        config(),
    )

    client = FakeGoogleCalendarClient()

    drifted = config(calendar_id="another-calendar")

    result = dispatch(
        repo,
        make_calendar_adapter(
            repo,
            client,
            calendar_config=drifted,
        ),
        operation_id,
    )

    assert result.adapter_invoked is True
    assert result.adapter_outcome is not None
    assert result.adapter_outcome.kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE

    assert client.get_count == 0
    assert client.insert_count == 0


def test_fresh_adapter_reconciles_after_process_recovery(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
) -> None:
    operation_id = claim_calendar(
        repo,
        config(),
    )

    client = FakeGoogleCalendarClient()

    first_adapter = make_calendar_adapter(
        repo,
        client,
    )

    result = dispatch(
        repo,
        first_adapter,
        operation_id,
    )

    assert result.adapter_invoked is True
    assert result.adapter_outcome is not None
    assert result.adapter_outcome.kind is AdapterOutcomeKind.SUCCESS
    assert client.insert_count == 1

    # Simulate loss of the worker after Google accepted the mutation,
    # but before the adapter result was durably persisted.
    recovery = recover_inflight_operation(
        repo,
        operation_id,
        now=NOW + timedelta(seconds=5),
    )

    assert recovery.reconciliation_required is True
    assert recovery.execute_calls == 0

    with sessions() as session:
        operation = session.get(
            OperationRecordORM,
            operation_id.value,
        )

        assert operation is not None
        assert operation.state == OperationState.UNKNOWN_OUTCOME.value

        unknown_revision = operation.revision

    # This is a completely new adapter instance. It must recover the
    # approved expected event from durable Phase 6A state rather than
    # from process-local execute() memory.
    fresh_adapter = make_calendar_adapter(
        repo,
        client,
    )

    reconciliation = reconcile_operation(
        repo,
        fresh_adapter,
        operation_id,
        expected_revision=unknown_revision,
        now=NOW + timedelta(seconds=6),
    )

    assert reconciliation.committed is True

    with sessions() as session:
        operation = session.get(
            OperationRecordORM,
            operation_id.value,
        )

        assert operation is not None
        assert operation.state == OperationState.CONFIRMED_SUCCESS.value

    # Original write happened once.
    assert client.insert_count == 1

    # Fresh reconciliation adapter only read provider state.
    assert fresh_adapter.trace.insert_count == 0
    assert fresh_adapter.trace.get_count == 1
