from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import phase6b_evaluation.real_acceptance_cli as real_acceptance_cli
import pytest
from phase6b_evaluation.manifest import FROZEN_MANIFEST_SHA256
from phase6b_evaluation.real_acceptance_cli import (
    TrustedRealAcceptanceAuthorization,
    _run_real_acceptance_with_client,
    run_real_acceptance,
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.approval.contracts import TrustedApproverContext
from aaos.approval.repository import ApprovalRepository
from aaos.integrations.google_calendar.canonicalization import (
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.client import (
    FakeGoogleCalendarClient,
    GoogleCalendarClientFactory,
    ObservedGoogleEvent,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
)
from aaos.integrations.google_calendar.errors import GoogleProviderErrorKind
from aaos.persistence.academic_models import Base
from aaos.persistence.approval_models import OperationRecordORM
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)

NOW = datetime(2026, 9, 15, 12, tzinfo=ZoneInfo("UTC"))
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DERIVATION_VERSION = "calendar-provider-id-v1"

APPROVER = TrustedApproverContext(
    "phase6b-real-acceptance-reviewer",
    "phase6b-real-acceptance-context",
)


def config(
    *,
    credential_path: str = "C:/trusted/phase6b-real-acceptance.json",
) -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id="phase6b-real-acceptance-test-calendar",
        expected_service_identity="phase6b-real-acceptance@example.invalid",
        oauth_scope=EVENTS_SCOPE,
        credential_path=credential_path,
        provider_adapter_version="calendar-adapter-v1",
        enabled=True,
    )


def request() -> CalendarCreateRequest:
    return CalendarCreateRequest(
        summary="AAOS Phase 6B real acceptance",
        start=NOW + timedelta(hours=2),
        end=NOW + timedelta(hours=2, minutes=15),
        timezone="UTC",
        description="AAOS phase6b acceptance marker",
        location="AAOS",
    )


def authorization(
    *,
    authorization_id: str = "phase6b-real-auth-test",
    manifest_fingerprint: str = FROZEN_MANIFEST_SHA256,
) -> TrustedRealAcceptanceAuthorization:
    return TrustedRealAcceptanceAuthorization(
        authorization_id=authorization_id,
        issued_at=NOW,
        manifest_fingerprint=manifest_fingerprint,
    )


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase6b-real-gate.db")
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


def test_normal_pytest_rejects_real_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "test_normal_pytest_rejects_real_acceptance",
    )

    monkeypatch.setattr(
        GoogleCalendarClientFactory,
        "build",
        lambda *_args, **_kwargs: pytest.fail(
            "ordinary pytest must never construct a real Google client"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="REAL_ACCEPTANCE_PYTEST_FORBIDDEN",
    ):
        run_real_acceptance(
            authorization=authorization(),
            request=request(),
        )


def test_missing_human_authorization_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "PYTEST_CURRENT_TEST",
        raising=False,
    )

    monkeypatch.setattr(
        GoogleCalendarClientFactory,
        "build",
        lambda *_args, **_kwargs: pytest.fail(
            "missing authorization must fail before client construction"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="REAL_ACCEPTANCE_AUTHORIZATION_REQUIRED",
    ):
        run_real_acceptance(
            authorization=cast(Any, None),
            request=request(),
        )


def test_acceptance_request_has_no_forbidden_fields() -> None:
    assert tuple(field.name for field in fields(CalendarCreateRequest)) == (
        "summary",
        "start",
        "end",
        "timezone",
        "description",
        "location",
    )

    canonical = canonicalize_calendar_request(
        request(),
        now=NOW,
    )

    assert canonical.summary == "AAOS Phase 6B real acceptance"
    assert canonical.description == "AAOS phase6b acceptance marker"
    assert canonical.location == "AAOS"

    duration = datetime.fromisoformat(
        canonical.end.replace("Z", "+00:00")
    ) - datetime.fromisoformat(canonical.start.replace("Z", "+00:00"))

    assert duration == timedelta(minutes=15)


def test_acceptance_requires_phase6a_executing(
    repo: ApprovalRepository,
    sessions: sessionmaker[Session],
) -> None:
    class OrderingClient(FakeGoogleCalendarClient):
        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            with sessions() as session:
                operations = tuple(session.execute(select(OperationRecordORM)).scalars())

                assert len(operations) == 1
                assert operations[0].state == "EXECUTING"
                assert operations[0].dispatch_started_at is not None

            return super().insert_event(
                calendar_id,
                event,
                provider_event_id,
            )

    client = OrderingClient()

    report = _run_real_acceptance_with_client(
        authorization=authorization(),
        request=request(),
        repository=repo,
        config=config(),
        client=client,
        approver_context=APPROVER,
        now=NOW,
        derivation_version=DERIVATION_VERSION,
    )

    assert client.insert_count == 1
    assert "CONFIRMED_SUCCESS" in json.dumps(
        asdict(report),
        default=str,
        sort_keys=True,
    )


def test_acceptance_timeout_reconciles_without_retry(
    repo: ApprovalRepository,
) -> None:
    client = FakeGoogleCalendarClient(
        insert_error=GoogleProviderErrorKind.TIMEOUT,
    )

    report = _run_real_acceptance_with_client(
        authorization=authorization(),
        request=request(),
        repository=repo,
        config=config(),
        client=client,
        approver_context=APPROVER,
        now=NOW,
        derivation_version=DERIVATION_VERSION,
    )

    serialized = json.dumps(
        asdict(report),
        default=str,
        sort_keys=True,
    )

    assert client.insert_count == 1
    assert client.get_count == 2
    assert "UNKNOWN_OUTCOME" in serialized


def test_acceptance_report_excludes_credentials_and_raw_provider_body(
    repo: ApprovalRepository,
) -> None:
    credential_marker = "AAOS_PHASE6B_CREDENTIAL_SECRET"
    provider_marker = "AAOS_PHASE6B_RAW_PROVIDER_SECRET"
    authorization_marker = "AAOS_PHASE6B_AUTHORIZATION_SECRET"

    class RawResponseClient(FakeGoogleCalendarClient):
        def insert_event(
            self,
            calendar_id: str,
            event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> ObservedGoogleEvent:
            super().insert_event(
                calendar_id,
                event,
                provider_event_id,
            )

            return cast(
                ObservedGoogleEvent,
                {
                    "raw_provider_body": provider_marker,
                },
            )

    client = RawResponseClient()

    report = _run_real_acceptance_with_client(
        authorization=authorization(
            authorization_id=authorization_marker,
        ),
        request=request(),
        repository=repo,
        config=config(
            credential_path=f"C:/trusted/{credential_marker}.json",
        ),
        client=client,
        approver_context=APPROVER,
        now=NOW,
        derivation_version=DERIVATION_VERSION,
    )

    serialized = json.dumps(
        asdict(report),
        default=str,
        sort_keys=True,
    )

    assert credential_marker not in serialized
    assert provider_marker not in serialized
    assert authorization_marker not in serialized


def test_authorized_operator_uses_trusted_factory_with_fake_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        real_acceptance_cli,
        "_stable_acceptance_root",
        lambda: (tmp_path / "stable-operator-root").resolve(),
    )

    # Deliberately simulate the separately gated operator environment.
    # The factory itself is replaced with a fake, so ordinary pytest
    # still performs zero live Google I/O.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    credential_path = (tmp_path / "phase6b-test-service-account.json").resolve()

    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_CALENDAR_ID",
        "phase6b-acceptance-calendar",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_SERVICE_IDENTITY",
        "phase6b-acceptance@example.invalid",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_CREDENTIAL_PATH",
        str(credential_path),
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_APPROVER_IDENTITY",
        "phase6b-human-reviewer",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_APPROVER_CONTEXT_ID",
        "phase6b-real-acceptance",
    )
    monkeypatch.setenv(
        "LOCALAPPDATA",
        str(tmp_path.resolve()),
    )

    fake_client = FakeGoogleCalendarClient()
    observed_configs: list[GoogleCalendarConfig] = []

    def fake_build(
        trusted_config: GoogleCalendarConfig,
    ) -> FakeGoogleCalendarClient:
        observed_configs.append(trusted_config)
        return fake_client

    monkeypatch.setattr(
        GoogleCalendarClientFactory,
        "build",
        staticmethod(fake_build),
    )

    current = datetime.now(ZoneInfo("UTC"))

    operator_request = CalendarCreateRequest(
        summary="AAOS Phase 6B real acceptance",
        start=current + timedelta(hours=1),
        end=current
        + timedelta(
            hours=1,
            minutes=15,
        ),
        timezone="UTC",
        description="AAOS phase6b acceptance marker",
        location="AAOS",
    )

    report = run_real_acceptance(
        authorization=authorization(),
        request=operator_request,
    )

    assert len(observed_configs) == 1

    trusted_config = observed_configs[0]

    assert trusted_config.calendar_id == "phase6b-acceptance-calendar"
    assert trusted_config.expected_service_identity == "phase6b-acceptance@example.invalid"
    assert trusted_config.credential_path == str(credential_path)
    assert trusted_config.oauth_scope == EVENTS_SCOPE
    assert trusted_config.provider_adapter_version == "calendar-adapter-v1"
    assert trusted_config.enabled is True

    assert fake_client.insert_count == 1
    assert report.action_state == "CONFIRMED_SUCCESS"
    assert report.operation_state == "CONFIRMED_SUCCESS"

    with pytest.raises(
        RuntimeError,
        match="REAL_ACCEPTANCE_ALREADY_ATTEMPTED",
    ):
        run_real_acceptance(
            authorization=authorization(),
            request=operator_request,
        )

    # The second invocation must fail before another factory build
    # and before another provider write.
    assert len(observed_configs) == 1
    assert fake_client.insert_count == 1


def _configure_real_operator_test_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_app_data: Path,
    credential_path: Path,
) -> None:
    monkeypatch.delenv(
        "PYTEST_CURRENT_TEST",
        raising=False,
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_CALENDAR_ID",
        "phase6b-acceptance-calendar",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_SERVICE_IDENTITY",
        "phase6b-acceptance@example.invalid",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_GOOGLE_CREDENTIAL_PATH",
        str(credential_path.resolve()),
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_APPROVER_IDENTITY",
        "phase6b-human-reviewer",
    )
    monkeypatch.setenv(
        "AAOS_PHASE6B_APPROVER_CONTEXT_ID",
        "phase6b-real-acceptance",
    )
    monkeypatch.setenv(
        "LOCALAPPDATA",
        str(local_app_data.resolve()),
    )


def _live_shaped_request() -> CalendarCreateRequest:
    current = datetime.now(ZoneInfo("UTC"))

    return CalendarCreateRequest(
        summary="AAOS Phase 6B real acceptance",
        start=current + timedelta(hours=1),
        end=current
        + timedelta(
            hours=1,
            minutes=15,
        ),
        timezone="UTC",
        description="AAOS phase6b acceptance marker",
        location="AAOS",
    )


def test_acceptance_one_shot_cannot_be_bypassed_by_localappdata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable_root = (tmp_path / "stable-acceptance-root").resolve()

    # Future implementation must consume this invariant helper.
    # Current implementation ignores it, making this test RED.
    monkeypatch.setattr(
        real_acceptance_cli,
        "_stable_acceptance_root",
        lambda: stable_root,
        raising=False,
    )

    first_env_root = tmp_path / "env-root-a"
    second_env_root = tmp_path / "env-root-b"

    _configure_real_operator_test_environment(
        monkeypatch,
        local_app_data=first_env_root,
        credential_path=tmp_path / "credential.json",
    )

    client = FakeGoogleCalendarClient()
    factory_calls: list[GoogleCalendarConfig] = []

    def fake_build(
        trusted_config: GoogleCalendarConfig,
    ) -> FakeGoogleCalendarClient:
        factory_calls.append(trusted_config)
        return client

    monkeypatch.setattr(
        GoogleCalendarClientFactory,
        "build",
        staticmethod(fake_build),
    )

    operator_request = _live_shaped_request()

    first = run_real_acceptance(
        authorization=authorization(),
        request=operator_request,
    )

    assert first.operation_state == "CONFIRMED_SUCCESS"
    assert client.insert_count == 1
    assert len(factory_calls) == 1

    # Changing an ordinary environment path must never manufacture a
    # fresh acceptance namespace.
    monkeypatch.setenv(
        "LOCALAPPDATA",
        str(second_env_root.resolve()),
    )

    second_error: RuntimeError | None = None

    try:
        run_real_acceptance(
            authorization=authorization(),
            request=operator_request,
        )
    except RuntimeError as error:
        second_error = error

    if second_error is not None:
        assert str(second_error) == ("REAL_ACCEPTANCE_ALREADY_ATTEMPTED")

    # Whether the duplicate call returns the existing terminal result
    # or fails closed, it must not even construct another provider
    # client and must never issue another insert.
    assert len(factory_calls) == 1
    assert client.insert_count == 1


def test_acceptance_restart_recovers_executing_by_read_only_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable_root = (tmp_path / "stable-recovery-root").resolve()

    monkeypatch.setattr(
        real_acceptance_cli,
        "_stable_acceptance_root",
        lambda: stable_root,
        raising=False,
    )

    _configure_real_operator_test_environment(
        monkeypatch,
        local_app_data=tmp_path / "irrelevant-env-root",
        credential_path=tmp_path / "credential.json",
    )

    client = FakeGoogleCalendarClient()
    factory_calls: list[GoogleCalendarConfig] = []

    def fake_build(
        trusted_config: GoogleCalendarConfig,
    ) -> FakeGoogleCalendarClient:
        factory_calls.append(trusted_config)
        return client

    monkeypatch.setattr(
        GoogleCalendarClientFactory,
        "build",
        staticmethod(fake_build),
    )

    operator_request = _live_shaped_request()

    original_record_adapter_outcome = real_acceptance_cli.record_adapter_outcome

    def simulate_process_loss(
        *args: object,
        **kwargs: object,
    ) -> object:
        del args, kwargs
        raise RuntimeError("SIMULATED_PROCESS_LOSS_AFTER_PROVIDER_WRITE")

    monkeypatch.setattr(
        real_acceptance_cli,
        "record_adapter_outcome",
        simulate_process_loss,
    )

    with pytest.raises(
        RuntimeError,
        match="SIMULATED_PROCESS_LOSS_AFTER_PROVIDER_WRITE",
    ):
        run_real_acceptance(
            authorization=authorization(),
            request=operator_request,
        )

    # Google was reached exactly once before simulated process loss.
    assert client.insert_count == 1

    monkeypatch.setattr(
        real_acceptance_cli,
        "record_adapter_outcome",
        original_record_adapter_outcome,
    )

    # Restart must recover EXECUTING -> UNKNOWN_OUTCOME and perform
    # reconciliation only. It must never dispatch/insert again.
    recovered = run_real_acceptance(
        authorization=authorization(),
        request=operator_request,
    )

    assert recovered.action_state == "CONFIRMED_SUCCESS"
    assert recovered.operation_state == "CONFIRMED_SUCCESS"

    assert client.insert_count == 1

    # First execution precondition read + restart reconciliation read.
    assert client.get_count == 2
