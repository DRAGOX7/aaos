from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import aaos.integrations.google_calendar.client as calendar_client
from aaos.integrations.google_calendar.canonicalization import (
    canonicalize_calendar_request,
)
from aaos.integrations.google_calendar.client import (
    REAL_ADAPTER_UNAVAILABLE,
    GoogleCalendarClientFactory,
)
from aaos.integrations.google_calendar.contracts import (
    CalendarCreateRequest,
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
)
from aaos.integrations.google_calendar.errors import (
    GoogleProviderError,
    GoogleProviderErrorKind,
)

EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
ADAPTER_VERSION = "calendar-adapter-v1"
TRUSTED_CALENDAR = "phase6b-test-calendar"
TRUSTED_IDENTITY = "phase6b-test-service@example.invalid"
PROVIDER_EVENT_ID = "abcde"
NOW = datetime(2026, 9, 14, 10, tzinfo=ZoneInfo("UTC"))


def config(
    tmp_path: Path,
    *,
    enabled: bool = True,
    credential_path: Path | None = None,
) -> GoogleCalendarConfig:
    return GoogleCalendarConfig(
        calendar_id=TRUSTED_CALENDAR,
        expected_service_identity=TRUSTED_IDENTITY,
        oauth_scope=EVENTS_SCOPE,
        credential_path=str(credential_path or (tmp_path / "service-account.json")),
        provider_adapter_version=ADAPTER_VERSION,
        enabled=enabled,
    )


def event() -> CanonicalCalendarEvent:
    return canonicalize_calendar_request(
        CalendarCreateRequest(
            summary="Task 4 factory test",
            start=NOW + timedelta(hours=2),
            end=NOW + timedelta(hours=3),
            timezone="UTC",
            description="No live Google request",
            location="AAOS test",
        ),
        now=NOW,
    )


def observed(
    canonical_event: CanonicalCalendarEvent,
    provider_event_id: str = PROVIDER_EVENT_ID,
) -> dict[str, object]:
    return {
        "provider_event_id": provider_event_id,
        "summary": canonical_event.summary,
        "start": canonical_event.start,
        "end": canonical_event.end,
        "timezone": canonical_event.timezone,
        "description": canonical_event.description,
        "location": canonical_event.location,
    }


class NarrowSdk:
    def __init__(self) -> None:
        self.get_calls = 0
        self.insert_calls = 0
        self.last_calendar_id: str | None = None

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> object | None:
        self.get_calls += 1
        self.last_calendar_id = calendar_id
        return None

    def insert_event(
        self,
        calendar_id: str,
        canonical_event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> object:
        self.insert_calls += 1
        self.last_calendar_id = calendar_id
        return observed(canonical_event, provider_event_id)


class ProviderResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class ProviderHttpError(RuntimeError):
    def __init__(self, status: int, *, reason: str | None = None) -> None:
        super().__init__("provider failure")
        self.resp = ProviderResponse(status)
        self.content = json.dumps(
            {"error": {"errors": ([] if reason is None else [{"reason": reason}])}}
        ).encode("utf-8")


def test_http_408_insert_is_timeout_not_authoritative_failure(
    tmp_path: Path,
) -> None:
    class TimeoutSdk(NarrowSdk):
        def insert_event(
            self,
            calendar_id: str,
            canonical_event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> object:
            raise ProviderHttpError(408)

    client = GoogleCalendarClientFactory.build(
        config(tmp_path),
        sdk=TimeoutSdk(),
    )

    with pytest.raises(GoogleProviderError) as exc_info:
        client.insert_event(
            TRUSTED_CALENDAR,
            event(),
            PROVIDER_EVENT_ID,
        )

    assert exc_info.value.kind is GoogleProviderErrorKind.TIMEOUT


def test_google_403_rate_limit_is_rate_limited(
    tmp_path: Path,
) -> None:
    class RateLimitedSdk(NarrowSdk):
        def insert_event(
            self,
            calendar_id: str,
            canonical_event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> object:
            raise ProviderHttpError(
                403,
                reason="rateLimitExceeded",
            )

    client = GoogleCalendarClientFactory.build(
        config(tmp_path),
        sdk=RateLimitedSdk(),
    )

    with pytest.raises(GoogleProviderError) as exc_info:
        client.insert_event(
            TRUSTED_CALENDAR,
            event(),
            PROVIDER_EVENT_ID,
        )

    assert exc_info.value.kind is GoogleProviderErrorKind.RATE_LIMITED


def assert_unavailable(call: Callable[[], object]) -> None:
    with pytest.raises(RuntimeError) as exc_info:
        call()
    assert str(exc_info.value) == REAL_ADAPTER_UNAVAILABLE


def test_disabled_config_is_unavailable(tmp_path: Path) -> None:
    sdk = NarrowSdk()
    selected = config(tmp_path, enabled=False)

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected, sdk=sdk))

    assert sdk.get_calls == 0
    assert sdk.insert_calls == 0


def test_missing_credential_is_unavailable(tmp_path: Path) -> None:
    selected = config(
        tmp_path,
        credential_path=tmp_path / "missing-service-account.json",
    )

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected))


def test_malformed_credential_is_unavailable(tmp_path: Path) -> None:
    credential = tmp_path / "malformed-service-account.json"
    credential.write_text("{not-json", encoding="utf-8")
    selected = config(tmp_path, credential_path=credential)

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected))


def test_service_identity_mismatch_is_unavailable(tmp_path: Path) -> None:
    credential = tmp_path / "wrong-service-account.json"
    credential.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "unexpected-service@example.invalid",
            }
        ),
        encoding="utf-8",
    )
    selected = config(tmp_path, credential_path=credential)

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected))


def test_scope_mismatch_is_unavailable(tmp_path: Path) -> None:
    selected = config(tmp_path)

    # Simulate trusted-runtime drift after the frozen contract was originally
    # constructed. The factory must revalidate rather than trusting prior
    # construction alone.
    object.__setattr__(
        selected,
        "oauth_scope",
        "https://www.googleapis.com/auth/calendar",
    )

    sdk = NarrowSdk()

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected, sdk=sdk))

    assert sdk.get_calls == 0
    assert sdk.insert_calls == 0


def test_calendar_mismatch_is_unavailable(tmp_path: Path) -> None:
    sdk = NarrowSdk()
    client = GoogleCalendarClientFactory.build(config(tmp_path), sdk=sdk)

    assert_unavailable(lambda: client.get_event("different-calendar", PROVIDER_EVENT_ID))

    assert sdk.get_calls == 0
    assert sdk.insert_calls == 0


def test_real_factory_never_falls_back_to_fake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_constructed = False

    def forbidden_fake_init(*args: object, **kwargs: object) -> None:
        nonlocal fake_constructed
        del args, kwargs
        fake_constructed = True
        raise AssertionError("real-client failure selected the fake client")

    monkeypatch.setattr(
        calendar_client.FakeGoogleCalendarClient,
        "__init__",
        forbidden_fake_init,
    )

    selected = config(
        tmp_path,
        credential_path=tmp_path / "missing-service-account.json",
    )

    assert_unavailable(lambda: GoogleCalendarClientFactory.build(selected))

    assert fake_constructed is False


def test_factory_uses_only_two_sdk_operations(tmp_path: Path) -> None:
    sdk = NarrowSdk()
    selected = config(tmp_path)
    client = GoogleCalendarClientFactory.build(selected, sdk=sdk)

    assert client.get_event(TRUSTED_CALENDAR, PROVIDER_EVENT_ID) is None

    canonical_event = event()
    created = client.insert_event(
        TRUSTED_CALENDAR,
        canonical_event,
        PROVIDER_EVENT_ID,
    )

    assert created.provider_event_id == PROVIDER_EVENT_ID
    assert created.summary == canonical_event.summary
    assert created.start == canonical_event.start
    assert created.end == canonical_event.end
    assert created.timezone == canonical_event.timezone
    assert created.description == canonical_event.description
    assert created.location == canonical_event.location

    assert sdk.get_calls == 1
    assert sdk.insert_calls == 1
    assert sdk.last_calendar_id == TRUSTED_CALENDAR
