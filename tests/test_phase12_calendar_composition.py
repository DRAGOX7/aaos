"""Phase 12B: the production composition of the frozen 6A/6B Calendar parts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import PermissionClass
from aaos.approval.contracts import TrustedApproverContext
from aaos.config import settings_from_environment
from aaos.integrations.google_calendar.canonicalization import (
    canonical_event_arguments,
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.client import FakeGoogleCalendarClient
from aaos.integrations.google_calendar.composition import (
    ACTION_LIFETIME,
    APPROVAL_LIFETIME,
    CALENDAR_DERIVATION_VERSION,
    CalendarProposalsUnavailable,
    build_calendar_services,
    calendar_action_definition,
    calendar_capability_policy,
)
from aaos.integrations.google_calendar.contracts import CalendarCreateRequest
from aaos.integrations.google_calendar.setup import (
    PROVIDER_ADAPTER_VERSION,
    CalendarProposalsSetup,
    resolve_calendar_proposals,
)
from aaos.persistence.academic_models import Base
from aaos.persistence.database import create_database_engine, create_session_factory

UTC = ZoneInfo("UTC")
AMMAN = ZoneInfo("Asia/Amman")
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
CALENDAR = "0123456789abcdef@group.calendar.google.com"
SERVICE = "aaos-calendar@example-project.iam.gserviceaccount.com"


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(tmp_path / "phase12.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def on_setup(tmp_path: Path, key_text: str = "{}") -> CalendarProposalsSetup:
    key = tmp_path / "service-account.json"
    key.write_text(key_text, encoding="utf-8")
    environment = {
        "AAOS_CALENDAR_PROPOSALS": "on",
        "AAOS_LOCAL_APPROVER_IDENTITY": "owner",
        "AAOS_LOCAL_APPROVER_CONTEXT_ID": "aaos-laptop-1",
        "AAOS_GOOGLE_CALENDAR_ID": CALENDAR,
        "AAOS_GOOGLE_SERVICE_IDENTITY": SERVICE,
        "AAOS_GOOGLE_CREDENTIAL_PATH": str(key),
        "AAOS_CALENDAR_TIMEZONE": "Asia/Amman",
    }
    setup = resolve_calendar_proposals(environment, settings_from_environment(environment))
    assert setup.state == "ON"
    return setup


def request() -> CalendarCreateRequest:
    return CalendarCreateRequest(
        summary="SE440 study session",
        start=(NOW + timedelta(days=1)).astimezone(AMMAN),
        end=(NOW + timedelta(days=1, hours=1)).astimezone(AMMAN),
        timezone="Asia/Amman",
        description=None,
        location=None,
    )


# --- the action definition -------------------------------------------------------------------


def test_the_definition_has_the_frozen_6b_shape() -> None:
    definition = calendar_action_definition()
    assert definition.action_type.value == "CREATE_EVENT"
    assert definition.tool_name == "calendar_create_event"
    assert definition.tool_schema_version == "phase6b-calendar-v1"
    assert definition.required_permission is PermissionClass.LOCAL_STATE_WRITE
    assert definition.policy_version == "phase5-v1"
    assert definition.timeout_seconds == 10
    # Regression: the adapter's derivation version must equal the provider adapter version
    # the 7E task service stamps on every action, or each approval is refused unwritten.
    assert CALENDAR_DERIVATION_VERSION == PROVIDER_ADAPTER_VERSION == "calendar-adapter-v1"


def test_the_fingerprints_are_computed_not_placeholders_and_stable() -> None:
    first, second = calendar_action_definition(), calendar_action_definition()
    for value in (first.tool_definition_fingerprint, first.policy_fingerprint):
        assert len(value) == 64 and set(value) <= set("0123456789abcdef")
        assert len(set(value)) > 1  # not "6" * 64 or "7" * 64
    assert first.tool_definition_fingerprint == second.tool_definition_fingerprint
    assert first.policy_fingerprint == second.policy_fingerprint
    assert first.tool_definition_fingerprint != first.policy_fingerprint


def test_the_validator_accepts_only_the_closed_calendar_schema() -> None:
    validate = calendar_action_definition()._argument_validator  # the frozen private name
    good = dict(canonical_event_arguments(canonicalize_calendar_request(request(), now=NOW)))
    assert set(validate(good)) == set(good)
    with pytest.raises((TypeError, ValueError)):
        validate({**good, "attendees": ["someone@example.com"]})
    with pytest.raises((TypeError, ValueError)):
        validate({key: value for key, value in good.items() if key != "end"})
    with pytest.raises((TypeError, ValueError)):
        validate({**good, "summary": 5})


def test_the_policy_allows_only_local_state_writes() -> None:
    policy = calendar_capability_policy()
    assert policy.granted_permissions == frozenset({PermissionClass.LOCAL_STATE_WRITE})
    assert policy.policy_version == calendar_action_definition().policy_version


# --- building the services -------------------------------------------------------------------


def test_services_are_built_from_an_on_setup_with_the_owner_approved_lifetimes(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    assert APPROVAL_LIFETIME == timedelta(hours=24)
    assert ACTION_LIFETIME == timedelta(hours=25)
    client = FakeGoogleCalendarClient()
    services = build_calendar_services(
        sessions, on_setup(tmp_path), client_builder=lambda config: client
    )
    detail = services.proposals.create_calendar_task(
        request(), idempotency_key="p12-1", idempotency_scope="ask-1", now=NOW
    )
    assert detail.approval is not None
    assert detail.approval.approval_expires_at - detail.approval.issued_at == timedelta(hours=24)
    assert client.insert_count == 0  # a proposal writes nothing


def test_a_setup_that_is_not_on_is_refused(tmp_path: Path, sessions: sessionmaker[Session]) -> None:
    for state in ("OFF", "MISCONFIGURED"):
        with pytest.raises(CalendarProposalsUnavailable):
            build_calendar_services(
                sessions,
                CalendarProposalsSetup(state=state),  # type: ignore[arg-type]
                client_builder=lambda config: FakeGoogleCalendarClient(),
            )


def test_a_client_that_cannot_be_built_never_falls_back_to_the_fake(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    # The real factory refuses an unreadable key file; nothing replaces it.
    with pytest.raises(CalendarProposalsUnavailable):
        build_calendar_services(sessions, on_setup(tmp_path, key_text="not json"))

    def broken(config: object) -> FakeGoogleCalendarClient:
        raise RuntimeError("boom")

    with pytest.raises(CalendarProposalsUnavailable) as caught:
        build_calendar_services(sessions, on_setup(tmp_path), client_builder=broken)
    assert caught.value.__cause__ is None


def test_the_approver_is_the_configured_trusted_context(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    setup = on_setup(tmp_path)
    services = build_calendar_services(
        sessions, setup, client_builder=lambda config: FakeGoogleCalendarClient()
    )
    assert services.approver == TrustedApproverContext("owner", "aaos-laptop-1")
    assert CALENDAR not in repr(services) and SERVICE not in repr(services)
