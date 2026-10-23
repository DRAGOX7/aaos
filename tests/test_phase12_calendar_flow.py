"""Phase 12C: the served app proposes, and writes only after one human approval.

Every test uses the fake Google client: no real Calendar write happens here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from aaos.config import settings_from_environment
from aaos.integrations.google_calendar.client import FakeGoogleCalendarClient
from aaos.integrations.google_calendar.composition import build_calendar_services
from aaos.integrations.google_calendar.contracts import CalendarCreateRequest
from aaos.integrations.google_calendar.errors import GoogleProviderErrorKind
from aaos.integrations.google_calendar.setup import resolve_calendar_proposals
from aaos.main import create_app
from aaos.persistence.academic_models import Base
from aaos.persistence.database import create_database_engine, create_session_factory

AMMAN = ZoneInfo("Asia/Amman")
CALENDAR = "0123456789abcdef@group.calendar.google.com"
SERVICE = "aaos-calendar@example-project.iam.gserviceaccount.com"
LOCAL_PAGE = {"Host": "localhost:8000", "Origin": "http://localhost:3000"}


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(tmp_path / "phase12-flow.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    key = tmp_path / "service-account.json"
    key.write_text("{}", encoding="utf-8")
    return {
        "AAOS_CALENDAR_PROPOSALS": "on",
        "AAOS_LOCAL_APPROVER_IDENTITY": "owner",
        "AAOS_LOCAL_APPROVER_CONTEXT_ID": "aaos-laptop-1",
        "AAOS_GOOGLE_CALENDAR_ID": CALENDAR,
        "AAOS_GOOGLE_SERVICE_IDENTITY": SERVICE,
        "AAOS_GOOGLE_CREDENTIAL_PATH": str(key),
        "AAOS_CALENDAR_TIMEZONE": "Asia/Amman",
    }


class World:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        environment: dict[str, str],
        client: FakeGoogleCalendarClient,
    ) -> None:
        self.sessions = sessions
        self.environment = environment
        self.client = client
        settings = settings_from_environment(environment)
        self.app = TestClient(
            create_app(
                settings,
                session_factory=sessions,
                calendar_environment=environment,
                calendar_client_builder=lambda config: client,
            )
        )
        # The agent creates proposals through the same frozen task service; tests call it
        # directly instead of going through a model (only possible when the setup is ON).
        setup = resolve_calendar_proposals(environment, settings)
        self.proposals = (
            build_calendar_services(sessions, setup, client_builder=lambda config: client).proposals
            if setup.state == "ON"
            else None
        )

    def propose(self, key: str, *, now: datetime | None = None) -> dict[str, Any]:
        assert self.proposals is not None
        now = now or datetime.now(timezone.utc)
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
        detail = self.proposals.create_calendar_task(
            CalendarCreateRequest(
                summary="SE440 study session",
                start=start.astimezone(AMMAN),
                end=(start + timedelta(hours=1)).astimezone(AMMAN),
                timezone="Asia/Amman",
                description=None,
                location=None,
            ),
            idempotency_key=key,
            idempotency_scope="ask-phase12",
            now=now,
        )
        assert detail.approval is not None
        return {"task_id": detail.task_id, "approval_id": detail.approval.approval_id}

    def decide(self, approval_id: str, decision: str, body: object | None = None) -> Any:
        return self.app.post(
            f"/api/v1/approvals/{approval_id}/{decision}",
            json={} if body is None else body,
            headers=LOCAL_PAGE,
        )

    def task(self, task_id: str) -> dict[str, Any]:
        return self.app.get(f"/api/v1/tasks/{task_id}").json()  # type: ignore[no-any-return]

    def calendar_health(self) -> dict[str, Any]:
        components = self.app.get("/api/v1/system-health").json()["components"]
        return next(c for c in components if c["name"] == "GOOGLE_CALENDAR")


def _world(sessions, environment, client=None) -> World:  # type: ignore[no-untyped-def]
    return World(sessions, environment, client or FakeGoogleCalendarClient())


# --- one click, one write --------------------------------------------------------------------


def test_a_proposal_writes_nothing_until_approved_then_exactly_one_event(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    world = _world(sessions, environment)
    proposal = world.propose("p12-approve")
    assert world.client.insert_count == 0
    assert world.task(proposal["task_id"])["approval"]["status"] == "PENDING"

    response = world.decide(proposal["approval_id"], "approve")
    assert response.status_code == 200, response.text
    assert world.client.insert_count == 1
    assert world.task(proposal["task_id"])["status"] == "CONFIRMED_SUCCESS"


def test_reject_writes_nothing(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    world = _world(sessions, environment)
    proposal = world.propose("p12-reject")
    assert world.decide(proposal["approval_id"], "reject").status_code == 200
    assert world.client.insert_count == 0
    assert world.task(proposal["task_id"])["approval"]["status"] == "REJECTED"


def test_repeated_and_opposite_decisions_are_refused_and_write_nothing_more(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    world = _world(sessions, environment)
    proposal = world.propose("p12-twice")
    assert world.decide(proposal["approval_id"], "approve").status_code == 200
    assert world.decide(proposal["approval_id"], "approve").status_code >= 400
    assert world.decide(proposal["approval_id"], "reject").status_code >= 400
    assert world.client.insert_count == 1


def test_a_proposal_past_its_24_hour_window_cannot_be_approved(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    world = _world(sessions, environment)
    created = datetime.now(timezone.utc) - timedelta(hours=24, minutes=5)
    proposal = world.propose("p12-expired", now=created)
    assert world.decide(proposal["approval_id"], "approve").status_code >= 400
    assert world.client.insert_count == 0


def test_a_lost_reply_is_never_shown_as_success_and_never_written_again(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    # The insert times out. The frozen adapter then reads the event back: the fake has no
    # event, so the outcome is settled as a confirmed failure; with no read either, it
    # would stay UNKNOWN_OUTCOME (covered by the frozen 6B tests). Never success, never twice.
    client = FakeGoogleCalendarClient(insert_error=GoogleProviderErrorKind.TIMEOUT)
    world = _world(sessions, environment, client)
    proposal = world.propose("p12-unknown")
    world.decide(proposal["approval_id"], "approve")
    detail = world.task(proposal["task_id"])
    assert detail["status"] in {"CONFIRMED_FAILURE", "UNKNOWN_OUTCOME"}
    assert client.get_count >= 1  # the outcome was checked, not assumed
    attempts = client.insert_count
    assert attempts <= 1
    assert world.decide(proposal["approval_id"], "approve").status_code >= 400
    assert client.insert_count == attempts


def test_the_browser_cannot_send_identity_or_arguments(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    world = _world(sessions, environment)
    proposal = world.propose("p12-browser")
    for body in (
        {"approver_identity": "someone-else"},
        {"calendar_id": "primary"},
        {"summary": "changed"},
    ):
        assert world.decide(proposal["approval_id"], "approve", body).status_code == 422
    assert world.client.insert_count == 0
    foreign = world.app.post(
        f"/api/v1/approvals/{proposal['approval_id']}/approve",
        json={},
        headers={"Host": "localhost:8000", "Origin": "https://evil.example"},
    )
    assert foreign.status_code == 403
    assert world.client.insert_count == 0


# --- off by default, and health --------------------------------------------------------------


def test_the_served_app_shows_the_calendar_state_in_system_health(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    assert _world(sessions, environment).calendar_health()["status"] == "HEALTHY"
    assert _world(sessions, environment).calendar_health()["detail_code"] == (
        "CALENDAR_PROPOSALS_READY"
    )

    broken = {**environment, "AAOS_GOOGLE_CALENDAR_ID": "primary"}
    misconfigured = _world(sessions, broken).calendar_health()
    assert misconfigured["status"] == "DEGRADED"
    assert misconfigured["detail_code"] == "CALENDAR_PROPOSALS_MISCONFIGURED"

    off = TestClient(
        create_app(settings_from_environment({}), session_factory=sessions, calendar_environment={})
    )
    calendar = next(
        c
        for c in off.get("/api/v1/system-health").json()["components"]
        if c["name"] == "GOOGLE_CALENDAR"
    )
    assert calendar["status"] == "NOT_CONFIGURED"


def test_off_builds_no_client_and_misconfigured_builds_none(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    built: list[object] = []

    def recording_builder(config: object) -> FakeGoogleCalendarClient:
        built.append(config)
        return FakeGoogleCalendarClient()

    create_app(
        settings_from_environment({}),
        session_factory=sessions,
        calendar_environment={},
        calendar_client_builder=recording_builder,
    )
    broken = {**environment, "AAOS_GOOGLE_SERVICE_IDENTITY": "person@gmail.com"}
    create_app(
        settings_from_environment(broken),
        session_factory=sessions,
        calendar_environment=broken,
        calendar_client_builder=recording_builder,
    )
    assert built == []


def test_a_client_the_factory_refuses_leaves_proposals_off(
    sessions: sessionmaker[Session], environment: dict[str, str]
) -> None:
    def refusing(config: object) -> FakeGoogleCalendarClient:
        raise RuntimeError("refused")

    app = TestClient(
        create_app(
            settings_from_environment(environment),
            session_factory=sessions,
            calendar_environment=environment,
            calendar_client_builder=refusing,
        )
    )
    components = app.get("/api/v1/system-health").json()["components"]
    calendar = next(c for c in components if c["name"] == "GOOGLE_CALENDAR")
    assert calendar["detail_code"] == "CALENDAR_PROPOSALS_MISCONFIGURED"


def test_no_calendar_or_credential_value_reaches_the_logs_or_api(
    sessions: sessionmaker[Session],
    environment: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        world = _world(sessions, environment)
        proposal = world.propose("p12-secrets")
        world.decide(proposal["approval_id"], "approve")
        broken = {**environment, "AAOS_GOOGLE_CALENDAR_ID": "someone@gmail.com"}
        _world(sessions, broken)
    shown = (
        caplog.text
        + str(world.task(proposal["task_id"]))
        + world.app.get("/api/v1/system-health").text
    )
    for value in (
        CALENDAR,
        SERVICE,
        environment["AAOS_GOOGLE_CREDENTIAL_PATH"],
        "someone@gmail.com",
        "aaos-laptop-1",
    ):
        assert value not in shown
    assert "CALENDAR PROPOSALS ACTIVE" in caplog.text
    assert "AAOS_GOOGLE_CALENDAR_ID" in caplog.text  # the misconfigured setting is named
