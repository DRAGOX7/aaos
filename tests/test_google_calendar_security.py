from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from aaos.agent.contracts import JSONValue
from aaos.approval.contracts import (
    ActionType,
    AdapterOutcomeKind,
    ExecutableAction,
    OperationId,
    RemotePrecondition,
    canonicalize_arguments,
)
from aaos.integrations.google_calendar.adapter import GoogleCalendarAdapter
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
    derive_provider_event_id,
)
from aaos.integrations.google_calendar.client import (
    REAL_ADAPTER_UNAVAILABLE,
    GoogleCalendarClient,
    GoogleCalendarClientFactory,
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
ADAPTER_VERSION = "calendar-adapter-v1"
TRUSTED_CALENDAR = "phase6b-security-calendar"
TRUSTED_IDENTITY = "phase6b-security-service@example.invalid"


@dataclass
class SecurityCounters:
    unauthorized_write_count: int = 0
    wrong_calendar_write_count: int = 0
    credential_exposure_count: int = 0
    raw_response_exposure_count: int = 0
    write_retry_count: int = 0


def config(
    *,
    calendar_id: str = TRUSTED_CALENDAR,
    credential_path: str = "C:/trusted/not-opened.json",
) -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id=calendar_id,
        expected_service_identity=TRUSTED_IDENTITY,
        oauth_scope=EVENTS_SCOPE,
        credential_path=credential_path,
        provider_adapter_version=ADAPTER_VERSION,
        enabled=True,
    )


def event() -> CanonicalCalendarEvent:
    local_now = NOW.astimezone(ZoneInfo("UTC"))
    return canonicalize_calendar_request(
        CalendarCreateRequest(
            summary="Task 5 security probe",
            start=local_now + timedelta(hours=2),
            end=local_now + timedelta(hours=3),
            timezone="UTC",
            description="Closed authority test",
            location="AAOS",
        ),
        now=NOW,
    )


def action_from_payload(
    payload: Mapping[str, JSONValue],
    *,
    operation_id: OperationId = OperationId("task5-operation"),
    target_config: GoogleCalendarConfig | None = None,
) -> ExecutableAction:
    selected_config = target_config or config()
    target = calendar_target(selected_config)

    return ExecutableAction(
        target_identity=target,
        action_type=ActionType("CREATE_EVENT"),
        canonical_arguments=canonicalize_arguments(payload).bytes,
        operation_id=operation_id,
        remote_precondition=RemotePrecondition(
            "google-calendar",
            target.resource,
            DERIVATION_VERSION,
            "absent",
        ),
    )


def valid_action(
    *,
    operation_id: OperationId = OperationId("task5-operation"),
    target_config: GoogleCalendarConfig | None = None,
) -> ExecutableAction:
    return action_from_payload(
        dict(canonical_event_arguments(event())),
        operation_id=operation_id,
        target_config=target_config,
    )


class CountingClient:
    def __init__(
        self,
        counters: SecurityCounters,
        *,
        trusted_calendar_id: str = TRUSTED_CALENDAR,
        insert_error: GoogleProviderErrorKind | None = None,
        writes_authorized: bool = True,
    ) -> None:
        self.counters = counters
        self.trusted_calendar_id = trusted_calendar_id
        self.insert_error = insert_error
        self.writes_authorized = writes_authorized
        self.get_count = 0
        self.insert_count = 0
        self.last_provider_event_id: str | None = None

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> ObservedGoogleEvent | None:
        self.get_count += 1
        self.last_provider_event_id = provider_event_id
        return None

    def insert_event(
        self,
        calendar_id: str,
        canonical_event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        self.insert_count += 1
        self.last_provider_event_id = provider_event_id

        if not self.writes_authorized:
            self.counters.unauthorized_write_count += 1
        if calendar_id != self.trusted_calendar_id:
            self.counters.wrong_calendar_write_count += 1

        if self.insert_count > 1:
            self.counters.write_retry_count += 1

        if self.insert_error is not None:
            raise GoogleProviderError(
                self.insert_error,
                "safe provider failure",
            )

        return ObservedGoogleEvent(
            provider_event_id=provider_event_id,
            summary=canonical_event.summary,
            start=canonical_event.start,
            end=canonical_event.end,
            timezone=canonical_event.timezone,
            description=canonical_event.description,
            location=canonical_event.location,
        )


def adapter_for(
    client: GoogleCalendarClient,
    *,
    expected_action: ExecutableAction | None = None,
) -> GoogleCalendarAdapter:
    selected = expected_action or valid_action()

    def load_expected(operation_id: OperationId) -> ExecutableAction | None:
        if operation_id != selected.operation_id:
            return None
        return selected

    return GoogleCalendarAdapter(
        client,
        config(),
        derivation_version=DERIVATION_VERSION,
        expected_action_loader=load_expected,
    )


FORBIDDEN_ACTION_FIELDS: tuple[tuple[str, JSONValue], ...] = (
    ("calendar_id", "attacker-calendar"),
    ("credential_path", "C:/attacker/credential.json"),
    ("oauth_scope", "https://www.googleapis.com/auth/calendar"),
    ("expected_service_identity", "attacker@example.invalid"),
    ("provider_event_id", "abcde"),
    ("attendees", [{"email": "attacker@example.invalid"}]),
    ("recurrence", ["RRULE:FREQ=DAILY"]),
    ("reminders", {"useDefault": False}),
    ("conferenceData", {"createRequest": {"requestId": "attacker"}}),
    ("sendUpdates", "all"),
    ("attachments", [{"fileUrl": "https://example.invalid/file"}]),
    ("extendedProperties", {"private": {"authority": "attacker"}}),
    ("arbitrary_google_json", {"any": {"nested": ["provider", "payload"]}}),
    ("api_method", "events.delete"),
    ("provider_client", {"call": "raw-provider-authority"}),
)


@pytest.mark.parametrize(("field_name", "field_value"), FORBIDDEN_ACTION_FIELDS)
def test_unapproved_action_authority_is_rejected_before_provider(
    field_name: str,
    field_value: JSONValue,
) -> None:
    counters = SecurityCounters()
    client = CountingClient(
        counters,
        writes_authorized=False,
    )
    selected_adapter = adapter_for(client)

    payload = dict(canonical_event_arguments(event()))
    payload[field_name] = field_value

    outcome = selected_adapter.execute(action_from_payload(payload))

    assert outcome.kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE
    assert client.get_count == 0
    assert client.insert_count == 0
    assert counters.unauthorized_write_count == 0
    assert counters.wrong_calendar_write_count == 0


def test_wrong_calendar_target_is_rejected_before_provider() -> None:
    counters = SecurityCounters()
    client = CountingClient(
        counters,
        writes_authorized=False,
    )
    selected_adapter = adapter_for(client)

    attacker_config = config(calendar_id="attacker-calendar")
    attacker_action = valid_action(target_config=attacker_config)

    outcome = selected_adapter.execute(attacker_action)

    assert outcome.kind is AdapterOutcomeKind.AUTHORITATIVE_FAILURE
    assert client.get_count == 0
    assert client.insert_count == 0
    assert counters.wrong_calendar_write_count == 0


def test_real_client_rejects_wrong_calendar_insert_before_provider_write(
    tmp_path: Path,
) -> None:
    counters = SecurityCounters()

    sdk = CountingClient(
        counters,
        trusted_calendar_id=TRUSTED_CALENDAR,
        writes_authorized=False,
    )

    selected = GoogleCalendarConfig(
        calendar_id=TRUSTED_CALENDAR,
        expected_service_identity=TRUSTED_IDENTITY,
        oauth_scope=EVENTS_SCOPE,
        credential_path=str(tmp_path / "not-opened.json"),
        provider_adapter_version=ADAPTER_VERSION,
        enabled=True,
    )

    client = GoogleCalendarClientFactory.build(
        selected,
        sdk=sdk,
    )

    with pytest.raises(RuntimeError) as exc_info:
        client.insert_event(
            "attacker-calendar",
            event(),
            "abcde",
        )

    assert str(exc_info.value) == REAL_ADAPTER_UNAVAILABLE
    assert sdk.insert_count == 0
    assert counters.unauthorized_write_count == 0
    assert counters.wrong_calendar_write_count == 0


def test_provider_event_id_is_derived_only_from_operation_id() -> None:
    counters = SecurityCounters()
    client = CountingClient(counters)
    operation_id = OperationId("task5-derived-provider-id")
    executable = valid_action(operation_id=operation_id)

    outcome = adapter_for(client, expected_action=executable).execute(executable)

    assert outcome.kind is AdapterOutcomeKind.SUCCESS
    assert client.last_provider_event_id == derive_provider_event_id(
        operation_id,
        derivation_version=DERIVATION_VERSION,
    )
    assert counters.wrong_calendar_write_count == 0


def test_raw_callable_cannot_enter_executable_action_arguments() -> None:
    payload: dict[str, object] = dict(canonical_event_arguments(event()))
    payload["raw_callable"] = lambda: None

    # Intentionally bypass the static JSONValue type to verify the runtime
    # canonicalization boundary rejects callable authority as well.
    with pytest.raises((TypeError, ValueError)):
        canonicalize_arguments(
            cast(Mapping[str, JSONValue], payload),
        )


class AuthorityProbeClient(CountingClient):
    def __init__(self, counters: SecurityCounters) -> None:
        super().__init__(counters)
        self.unauthorized_method_calls = 0

    def __call__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.unauthorized_method_calls += 1

    def request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.unauthorized_method_calls += 1

    def delete_event(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.unauthorized_method_calls += 1

    def update_event(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.unauthorized_method_calls += 1


def test_adapter_uses_only_narrow_provider_client_authority() -> None:
    counters = SecurityCounters()
    client = AuthorityProbeClient(counters)
    executable = valid_action()

    outcome = adapter_for(client, expected_action=executable).execute(executable)

    assert outcome.kind is AdapterOutcomeKind.SUCCESS
    assert client.get_count == 1
    assert client.insert_count == 1
    assert client.unauthorized_method_calls == 0
    assert counters.unauthorized_write_count == 0
    assert counters.wrong_calendar_write_count == 0


def test_real_client_exposes_no_generic_provider_operation(
    tmp_path: Path,
) -> None:
    counters = SecurityCounters()
    sdk = AuthorityProbeClient(counters)

    trusted_config = GoogleCalendarConfig(
        calendar_id=TRUSTED_CALENDAR,
        expected_service_identity=TRUSTED_IDENTITY,
        oauth_scope=EVENTS_SCOPE,
        credential_path=str(tmp_path / "not-opened.json"),
        provider_adapter_version=ADAPTER_VERSION,
        enabled=True,
    )

    client = GoogleCalendarClientFactory.build(
        trusted_config,
        sdk=sdk,
    )

    for name in (
        "delete_event",
        "update_event",
        "request",
        "execute",
        "events",
    ):
        assert not callable(getattr(client, name, None))

    assert sdk.unauthorized_method_calls == 0


def test_real_factory_failure_does_not_expose_credential_material(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_marker = "AAOS_TASK5_CREDENTIAL_SECRET_DO_NOT_EXPOSE"
    credential_path = tmp_path / f"{secret_marker}.json"

    selected = GoogleCalendarConfig(
        calendar_id=TRUSTED_CALENDAR,
        expected_service_identity=TRUSTED_IDENTITY,
        oauth_scope=EVENTS_SCOPE,
        credential_path=str(credential_path),
        provider_adapter_version=ADAPTER_VERSION,
        enabled=True,
    )

    counters = SecurityCounters()

    caplog.set_level(
        logging.DEBUG,
        logger="aaos.integrations.google_calendar.client",
    )
    caplog.clear()

    # Harness-integrity probe: prove a DEBUG-only record is visible before
    # relying on the credential marker scan below.
    debug_capture_probe = "AAOS_TASK5_DEBUG_CAPTURE_PROBE"
    logging.getLogger("aaos.integrations.google_calendar.client").debug(debug_capture_probe)
    assert any(
        debug_capture_probe in (record.getMessage() + repr(record.__dict__))
        for record in caplog.records
    )

    caplog.clear()

    with pytest.raises(RuntimeError) as exc_info:
        GoogleCalendarClientFactory.build(selected)

    exposed = secret_marker in str(exc_info.value) or any(
        secret_marker in (record.getMessage() + repr(record.__dict__)) for record in caplog.records
    )

    if exposed:
        counters.credential_exposure_count += 1

    assert str(exc_info.value) == REAL_ADAPTER_UNAVAILABLE
    assert counters.credential_exposure_count == 0


def test_raw_provider_value_never_escapes_adapter_evidence() -> None:
    secret_marker = "AAOS_TASK5_RAW_PROVIDER_SECRET_DO_NOT_EXPOSE"
    counters = SecurityCounters()

    class RawResponseClient:
        def __init__(self) -> None:
            self.insert_count = 0

        def get_event(
            self,
            calendar_id: str,
            provider_event_id: str,
        ) -> ObservedGoogleEvent | None:
            del calendar_id, provider_event_id
            return None

        def insert_event(
            self,
            calendar_id: str,
            canonical_event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> object:
            del calendar_id, canonical_event, provider_event_id
            self.insert_count += 1
            return {
                "raw_provider_payload": secret_marker,
            }

    client = RawResponseClient()
    outcome = adapter_for(cast(GoogleCalendarClient, client)).execute(valid_action())

    if secret_marker in outcome.evidence:
        counters.raw_response_exposure_count += 1

    assert outcome.kind is AdapterOutcomeKind.UNKNOWN
    assert client.insert_count == 1
    assert counters.raw_response_exposure_count == 0


def test_unknown_insert_has_zero_write_retry_and_reconciliation_is_read_only() -> None:
    counters = SecurityCounters()
    client = CountingClient(
        counters,
        insert_error=GoogleProviderErrorKind.TRANSPORT_ERROR,
    )
    executable = valid_action()
    selected_adapter = adapter_for(client, expected_action=executable)

    outcome = selected_adapter.execute(executable)

    assert outcome.kind in {
        AdapterOutcomeKind.UNKNOWN,
        AdapterOutcomeKind.TRANSPORT_FAILURE,
    }

    selected_adapter.reconcile(executable.operation_id)

    assert client.insert_count == 1
    assert counters.write_retry_count == 0
    assert counters.wrong_calendar_write_count == 0
