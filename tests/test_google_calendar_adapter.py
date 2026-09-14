from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aaos.approval.contracts import (
    ActionType,
    AdapterOutcomeKind,
    ExecutableAction,
    OperationId,
    RemotePrecondition,
)
from aaos.integrations.google_calendar.adapter import (
    CalendarAdapterExecutionTrace,
    GoogleCalendarAdapter,
    decode_calendar_action,
)
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
    derive_provider_event_id,
)
from aaos.integrations.google_calendar.client import (
    FakeGoogleCalendarClient,
    GoogleCalendarClient,
    ObservedGoogleEvent,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
    calendar_target,
)
from aaos.integrations.google_calendar.errors import (
    GoogleProviderError,
    GoogleProviderErrorKind,
)

NOW = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)
DERIVATION_VERSION = "calendar-provider-id-v1"
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def config(*, calendar_id: str = "phase6b-test-calendar") -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id=calendar_id,
        expected_service_identity="phase6b-test-service",
        oauth_scope=EVENTS_SCOPE,
        credential_path="C:/trusted/not-opened.json",
        provider_adapter_version="calendar-adapter-v1",
        enabled=True,
    )


def event(*, summary: str = "Architecture review") -> CanonicalCalendarEvent:
    local_now = NOW.astimezone(ZoneInfo("UTC"))
    return canonicalize_calendar_request(
        CalendarCreateRequest(
            summary=summary,
            start=local_now + timedelta(hours=2),
            end=local_now + timedelta(hours=3),
            timezone="UTC",
            description="Review the bounded design",
            location="Room 6B",
        ),
        now=NOW,
    )


def action(
    *,
    operation_id: OperationId = OperationId("operation-1"),
    calendar_config: GoogleCalendarConfig | None = None,
    canonical_event: CanonicalCalendarEvent | None = None,
    action_type: ActionType = ActionType("CREATE_EVENT"),
    precondition: RemotePrecondition | None = None,
) -> ExecutableAction:
    selected_config = calendar_config or config()
    selected_event = canonical_event or event()
    target = calendar_target(selected_config)
    from aaos.approval.contracts import canonicalize_arguments

    return ExecutableAction(
        target_identity=target,
        action_type=action_type,
        canonical_arguments=canonicalize_arguments(canonical_event_arguments(selected_event)).bytes,
        operation_id=operation_id,
        remote_precondition=precondition
        or RemotePrecondition(
            "google-calendar",
            target.resource,
            DERIVATION_VERSION,
            "absent",
        ),
    )


def observed(
    executable: ExecutableAction,
    *,
    summary: str | None = None,
) -> ObservedGoogleEvent:
    expected = decode_calendar_action(executable)
    return ObservedGoogleEvent(
        provider_event_id=derive_provider_event_id(
            executable.operation_id, derivation_version=DERIVATION_VERSION
        ),
        summary=expected.summary if summary is None else summary,
        start=expected.start,
        end=expected.end,
        timezone=expected.timezone,
        description=expected.description,
        location=expected.location,
    )


def adapter(client: GoogleCalendarClient) -> GoogleCalendarAdapter:
    return GoogleCalendarAdapter(client, config(), derivation_version=DERIVATION_VERSION)


def test_adapter_accepts_only_executable_action() -> None:
    client = FakeGoogleCalendarClient()
    calendar_adapter = adapter(client)

    with pytest.raises(TypeError):
        calendar_adapter.execute(object())  # type: ignore[arg-type]

    assert client.get_count == 0
    assert client.insert_count == 0


def test_absent_precondition_allows_one_insert() -> None:
    client = FakeGoogleCalendarClient()
    calendar_adapter = adapter(client)

    outcome = calendar_adapter.execute(action())

    assert outcome.kind is AdapterOutcomeKind.SUCCESS
    assert client.get_count == 1
    assert client.insert_count == 1
    assert len(client.events) == 1
    assert calendar_adapter.trace == CalendarAdapterExecutionTrace(1, 1, config().calendar_id)


def test_existing_event_returns_precondition_stale_and_zero_insert() -> None:
    executable = action()
    client = FakeGoogleCalendarClient(
        initial_events=((config().calendar_id, observed(executable)),)
    )

    outcome = adapter(client).execute(executable)

    assert outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE
    assert client.get_count == 1
    assert client.insert_count == 0


def test_precondition_changes_between_advisory_check_and_execute() -> None:
    executable = action()
    existing = observed(executable)

    class ChangesAfterAdvisory:
        def __init__(self) -> None:
            self.get_count = 0
            self.insert_count = 0

        def get_event(self, calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None:
            self.get_count += 1
            return None if self.get_count == 1 else existing

        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            self.insert_count += 1
            return existing

    client = ChangesAfterAdvisory()
    assert client.get_event(config().calendar_id, existing.provider_event_id) is None

    outcome = adapter(client).execute(executable)

    assert outcome.kind is AdapterOutcomeKind.PRECONDITION_STALE
    assert client.insert_count == 0


def test_provider_id_collision_never_generates_alternate_id() -> None:
    executable = action()
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.ALREADY_EXISTS)

    outcome = adapter(client).execute(executable)

    assert outcome.kind is AdapterOutcomeKind.UNKNOWN
    assert client.get_count == 1
    assert client.insert_count == 1
    assert len(client.events) == 0
    assert client.events == ()


def test_insert_success_maps_to_success() -> None:
    outcome = adapter(FakeGoogleCalendarClient()).execute(action())

    assert outcome.kind is AdapterOutcomeKind.SUCCESS
    assert outcome.evidence == "calendar event created"


@pytest.mark.parametrize(
    "kind",
    (
        GoogleProviderErrorKind.AUTHENTICATION_REJECTED,
        GoogleProviderErrorKind.PERMISSION_REJECTED,
        GoogleProviderErrorKind.INVALID_REQUEST,
    ),
)
def test_definite_rejection_maps_to_authoritative_failure(
    kind: GoogleProviderErrorKind,
) -> None:
    client = FakeGoogleCalendarClient(insert_error=kind)

    outcome = adapter(client).execute(action())

    assert outcome.kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE


def test_timeout_maps_to_unknown() -> None:
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TIMEOUT)

    outcome = adapter(client).execute(action())

    assert outcome.kind in {AdapterOutcomeKind.UNKNOWN, AdapterOutcomeKind.TRANSPORT_FAILURE}


def test_reconcile_is_read_only() -> None:
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TIMEOUT)
    calendar_adapter = adapter(client)
    executable = action()
    calendar_adapter.execute(executable)
    inserts_before = client.insert_count

    calendar_adapter.reconcile(executable.operation_id)

    assert client.insert_count == inserts_before
    assert client.get_count == 2


def test_exact_semantic_match_reconciles_success() -> None:
    client = FakeGoogleCalendarClient()
    calendar_adapter = adapter(client)
    executable = action()
    assert calendar_adapter.execute(executable).kind is AdapterOutcomeKind.SUCCESS

    outcome = calendar_adapter.reconcile(executable.operation_id)

    assert outcome.kind is AdapterOutcomeKind.SUCCESS


def test_mismatch_reconciliation_remains_unknown() -> None:
    executable = action()

    class MismatchAfterTimeout:
        def __init__(self) -> None:
            self.insert_count = 0
            self.get_count = 0

        def get_event(self, calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None:
            self.get_count += 1
            return None if self.get_count == 1 else observed(executable, summary="Other")

        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            self.insert_count += 1
            raise GoogleProviderError(GoogleProviderErrorKind.TIMEOUT, "safe timeout")

    client = MismatchAfterTimeout()
    calendar_adapter = adapter(client)
    calendar_adapter.execute(executable)

    outcome = calendar_adapter.reconcile(executable.operation_id)

    assert outcome.kind is AdapterOutcomeKind.UNKNOWN


def test_not_found_reconciliation_remains_unknown() -> None:
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TIMEOUT)
    calendar_adapter = adapter(client)
    executable = action()
    calendar_adapter.execute(executable)

    outcome = calendar_adapter.reconcile(executable.operation_id)

    assert outcome.kind is AdapterOutcomeKind.UNKNOWN


def test_no_write_retry_after_unknown() -> None:
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TRANSPORT_ERROR)
    calendar_adapter = adapter(client)
    executable = action()

    assert calendar_adapter.execute(executable).kind in {
        AdapterOutcomeKind.UNKNOWN,
        AdapterOutcomeKind.TRANSPORT_FAILURE,
    }
    calendar_adapter.reconcile(executable.operation_id)

    assert client.insert_count == 1


def test_raw_provider_error_is_not_returned() -> None:
    secret = "raw-provider-payload-secret"

    class RawFailureClient:
        def get_event(self, calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None:
            return None

        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            raise RuntimeError(secret)

    outcome = adapter(RawFailureClient()).execute(action())

    assert outcome.kind is AdapterOutcomeKind.UNKNOWN
    assert secret not in outcome.evidence
    assert set(field.name for field in fields(type(outcome))) == {"kind", "evidence"}
