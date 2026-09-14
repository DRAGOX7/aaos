from __future__ import annotations

import importlib
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
    RealGoogleCalendarClient,
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


def test_real_client_cannot_be_constructed_outside_trusted_factory() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        RealGoogleCalendarClient(
            NarrowSdk(),
            TRUSTED_CALENDAR,
        )

    assert str(exc_info.value) == REAL_ADAPTER_UNAVAILABLE


def test_google_403_rate_limit_in_later_error_entry_is_rate_limited(
    tmp_path: Path,
) -> None:
    class MultiReasonSdk(NarrowSdk):
        def insert_event(
            self,
            calendar_id: str,
            canonical_event: CanonicalCalendarEvent,
            provider_event_id: str,
        ) -> object:
            error = ProviderHttpError(
                403,
                reason="forbidden",
            )
            error.content = json.dumps(
                {
                    "error": {
                        "errors": [
                            {"reason": "forbidden"},
                            {"reason": "rateLimitExceeded"},
                        ]
                    }
                }
            ).encode("utf-8")
            raise error

    client = GoogleCalendarClientFactory.build(
        config(tmp_path),
        sdk=MultiReasonSdk(),
    )

    with pytest.raises(GoogleProviderError) as exc_info:
        client.insert_event(
            TRUSTED_CALENDAR,
            event(),
            PROVIDER_EVENT_ID,
        )

    assert exc_info.value.kind is GoogleProviderErrorKind.RATE_LIMITED


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


def test_official_sdk_transport_has_zero_hidden_calendar_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trusted_config = config(tmp_path)

    token_uri = "https://oauth2.example.invalid/token"
    calendar_uri = "https://www.googleapis.com/calendar/v3/calendars/test/events"

    requested_modules: list[str] = []
    pool_options: list[dict[str, object]] = []
    provider_calls: list[dict[str, object]] = []
    before_request_calls: list[tuple[str, str]] = []
    service_http: list[object] = []

    class FakeProviderResponse:
        status = 200
        reason = "OK"
        headers = {"content-type": "application/json"}
        data = b"{}"

    class FakePoolManager:
        def __init__(self, **kwargs: object) -> None:
            self._options = dict(kwargs)
            pool_options.append(self._options)

        def request(
            self,
            method: str,
            url: str,
            body: object = None,
            fields: object = None,
            headers: object = None,
            json: object = None,
            **kwargs: object,
        ) -> FakeProviderResponse:
            provider_calls.append(
                {
                    "method": method,
                    "url": url,
                    "body": body,
                    "fields": fields,
                    "headers": headers,
                    "json": json,
                    "pool_options": dict(self._options),
                    **kwargs,
                }
            )

            return FakeProviderResponse()

    class FakeCredential:
        service_account_email = trusted_config.expected_service_identity
        scopes = (trusted_config.oauth_scope,)

        def before_request(
            self,
            request: object,
            method: str,
            url: str,
            headers: object,
        ) -> None:
            before_request_calls.append(
                (
                    method,
                    url,
                )
            )

            refresh_request = request
            assert callable(refresh_request)

            refresh_request(
                token_uri,
                method="POST",
                body=b"test-assertion",
                headers={
                    "content-type": "application/x-www-form-urlencoded",
                },
            )

            assert isinstance(headers, dict)
            headers["authorization"] = "Bearer AAOS_TEST_TOKEN"

    class FakeCredentialsType:
        @staticmethod
        def from_service_account_info(
            info: dict[str, object],
            *,
            scopes: list[str],
        ) -> FakeCredential:
            del info

            assert scopes == [
                trusted_config.oauth_scope,
            ]

            return FakeCredential()

    class FakeServiceAccountModule:
        Credentials = FakeCredentialsType

    class FakeUrllib3Module:
        PoolManager = FakePoolManager

    class FakeEventsResource:
        pass

    class FakeService:
        def events(self) -> FakeEventsResource:
            return FakeEventsResource()

    class FakeDiscoveryModule:
        @staticmethod
        def build(
            api_name: str,
            version: str,
            *,
            http: object,
            cache_discovery: bool,
        ) -> FakeService:
            assert api_name == "calendar"
            assert version == "v3"
            assert cache_discovery is False

            service_http.append(http)

            return FakeService()

    modules: dict[str, object] = {
        "urllib3": FakeUrllib3Module,
        "google.oauth2.service_account": FakeServiceAccountModule,
        "googleapiclient.discovery": FakeDiscoveryModule,
    }

    def fake_import(name: str) -> object:
        requested_modules.append(name)
        return modules[name]

    monkeypatch.setattr(
        importlib,
        "import_module",
        fake_import,
    )

    calendar_client._build_official_sdk(
        trusted_config,
        {
            "type": "service_account",
            "client_email": trusted_config.expected_service_identity,
        },
    )

    assert "urllib3" in requested_modules
    assert "google.oauth2.service_account" in requested_modules
    assert "googleapiclient.discovery" in requested_modules

    assert "google.auth.transport.urllib3" not in requested_modules
    assert "google_auth_httplib2" not in requested_modules

    assert pool_options == [
        {
            "timeout": 5.0,
            "retries": False,
        },
        {
            "timeout": 10.0,
            "retries": False,
        },
    ]

    assert len(service_http) == 2

    request = getattr(
        service_http[1],
        "request",
        None,
    )
    assert callable(request)

    response, content = request(
        calendar_uri,
        method="POST",
        body=b"{}",
        headers={},
    )

    assert response.status == 200
    assert content == b"{}"

    assert before_request_calls == [
        (
            "POST",
            calendar_uri,
        )
    ]

    # Exactly two transport calls:
    # 1. one explicit credential refresh
    # 2. one Calendar request
    #
    # Neither transport boundary may retry or follow redirects.
    assert len(provider_calls) == 2

    refresh_call = provider_calls[0]

    assert refresh_call["method"] == "POST"
    assert refresh_call["url"] == token_uri
    assert refresh_call["retries"] is False
    assert refresh_call["redirect"] is False
    assert refresh_call["pool_options"] == {
        "timeout": 10.0,
        "retries": False,
    }

    calendar_call = provider_calls[1]

    assert calendar_call["method"] == "POST"
    assert calendar_call["url"] == calendar_uri
    assert calendar_call["body"] == b"{}"
    assert calendar_call["retries"] is False
    assert calendar_call["redirect"] is False
    assert calendar_call["pool_options"] == {
        "timeout": 10.0,
        "retries": False,
    }

    calendar_headers = calendar_call["headers"]

    assert isinstance(calendar_headers, dict)
    assert calendar_headers["authorization"] == "Bearer AAOS_TEST_TOKEN"


def test_fresh_service_account_refresh_never_logs_secret_material(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import logging
    from urllib.parse import parse_qs

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    trusted_config = config(tmp_path)

    token_uri = "https://oauth2.example.invalid/token"
    calendar_uri = "https://www.googleapis.com/calendar/v3/calendars/test/events"

    access_token = "AAOS_TEST_ACCESS_TOKEN_DO_NOT_LOG"

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    credential_info: dict[str, object] = {
        "type": "service_account",
        "client_email": trusted_config.expected_service_identity,
        "private_key": private_key_pem,
        "private_key_id": "aaos-test-key",
        "token_uri": token_uri,
        "client_id": "1234567890",
    }

    assertions: list[str] = []
    service_http: list[object] = []

    class FakePoolResponse:
        def __init__(
            self,
            *,
            data: bytes,
        ) -> None:
            self.status = 200
            self.reason = "OK"
            self.headers = {
                "content-type": "application/json",
            }
            self.data = data

    class FakePoolManager:
        def __init__(
            self,
            **kwargs: object,
        ) -> None:
            del kwargs

        def _send(
            self,
            method: str,
            url: str,
            body: object = None,
            headers: object = None,
            **kwargs: object,
        ) -> FakePoolResponse:
            del method, headers, kwargs

            if url == token_uri:
                if isinstance(body, bytes):
                    body_text = body.decode("utf-8")
                elif isinstance(body, str):
                    body_text = body
                else:
                    raise AssertionError("token refresh body must be bytes or str")

                parsed = parse_qs(body_text)

                assertion_values = parsed.get("assertion")
                assert assertion_values is not None
                assert len(assertion_values) == 1

                assertions.append(assertion_values[0])

                return FakePoolResponse(
                    data=json.dumps(
                        {
                            "access_token": access_token,
                            "expires_in": 3600,
                            "token_type": "Bearer",
                        }
                    ).encode("utf-8")
                )

            if url == calendar_uri:
                return FakePoolResponse(
                    data=b"{}",
                )

            raise AssertionError(f"unexpected provider URL: {url}")

        def request(
            self,
            method: str,
            url: str,
            body: object = None,
            headers: object = None,
            **kwargs: object,
        ) -> FakePoolResponse:
            return self._send(
                method,
                url,
                body=body,
                headers=headers,
                **kwargs,
            )

        def urlopen(
            self,
            method: str,
            url: str,
            body: object = None,
            headers: object = None,
            **kwargs: object,
        ) -> FakePoolResponse:
            return self._send(
                method,
                url,
                body=body,
                headers=headers,
                **kwargs,
            )

    class FakeUrllib3Module:
        PoolManager = FakePoolManager

    class FakeEventsResource:
        pass

    class FakeService:
        def events(self) -> FakeEventsResource:
            return FakeEventsResource()

    class FakeDiscoveryModule:
        @staticmethod
        def build(
            api_name: str,
            version: str,
            *,
            http: object,
            cache_discovery: bool,
        ) -> FakeService:
            assert api_name == "calendar"
            assert version == "v3"
            assert cache_discovery is False

            service_http.append(http)

            return FakeService()

    real_service_account_module = importlib.import_module("google.oauth2.service_account")
    real_google_urllib3_module = importlib.import_module("google.auth.transport.urllib3")

    modules: dict[str, object] = {
        "urllib3": FakeUrllib3Module,
        "google.oauth2.service_account": real_service_account_module,
        "google.auth.transport.urllib3": real_google_urllib3_module,
        "googleapiclient.discovery": FakeDiscoveryModule,
    }

    def fake_import(name: str) -> object:
        return modules[name]

    monkeypatch.setattr(
        importlib,
        "import_module",
        fake_import,
    )

    google_logger = logging.getLogger("google")

    # Simulate an application explicitly enabling Google/Auth DEBUG logging.
    # Google Auth's logger does not necessarily propagate to the root logger,
    # so attach pytest's capture handler directly.
    caplog.set_level(
        logging.DEBUG,
        logger="google",
    )
    google_logger.addHandler(caplog.handler)
    caplog.clear()

    try:
        calendar_client._build_official_sdk(
            trusted_config,
            credential_info,
        )

        assert len(service_http) == 2

        request = getattr(
            service_http[1],
            "request",
            None,
        )
        assert callable(request)

        request(
            calendar_uri,
            method="POST",
            body=b"{}",
            headers={},
        )
    finally:
        google_logger.removeHandler(caplog.handler)

    assert len(assertions) == 1

    signed_assertion = assertions[0]

    # Google Auth records request details as structured LogRecord extras.
    # Inspecting only record.getMessage() would miss secrets placed under
    # fields such as httpRequest.
    logged_record_state = "\n".join(repr(record.__dict__) for record in caplog.records)

    leaked_material = {
        "signed service-account assertion": signed_assertion,
        "private key": private_key_pem,
        "access token": access_token,
        "Authorization header": f"Bearer {access_token}",
    }

    for label, sensitive_value in leaked_material.items():
        if sensitive_value in logged_record_state:
            pytest.fail(f"{label} entered Google/Auth structured logging")


def test_raw_google_error_body_never_reaches_googleapiclient_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    google_http = importlib.import_module("googleapiclient.http")

    raw_marker = "AAOS_RAW_GOOGLE_ERROR_BODY_MUST_NEVER_LOG"
    raw_body = raw_marker.encode("utf-8")

    provider_calls: list[dict[str, object]] = []

    class FakeCredential:
        service_account_email = TRUSTED_IDENTITY
        scopes = (EVENTS_SCOPE,)

        def before_request(
            self,
            request: object,
            method: str,
            url: str,
            headers: object,
        ) -> None:
            del request, method, url, headers

    class FakeProviderResponse:
        status = 403
        reason = "Forbidden"
        headers = {
            "content-type": "text/plain",
        }
        data = raw_body

    class FakePool:
        def request(
            self,
            method: str,
            url: str,
            body: object = None,
            fields: object = None,
            headers: object = None,
            json: object = None,
            **kwargs: object,
        ) -> FakeProviderResponse:
            provider_calls.append(
                {
                    "method": method,
                    "url": url,
                    "body": body,
                    "fields": fields,
                    "headers": headers,
                    "json": json,
                    **kwargs,
                }
            )

            return FakeProviderResponse()

    compat_http_type = getattr(
        calendar_client,
        "_GoogleApiCompatHttp",
    )

    compat_http = compat_http_type(
        FakeCredential(),
        FakePool(),
    )

    def postproc(
        response: object,
        content: bytes,
    ) -> object:
        del response, content
        return object()

    google_http = importlib.import_module("googleapiclient.http")

    http_request_type = getattr(
        google_http,
        "HttpRequest",
    )
    assert callable(http_request_type)

    request = http_request_type(
        compat_http,
        postproc,
        "https://www.googleapis.com/calendar/v3/calendars/test/events/abcde",
        method="GET",
        headers={},
    )

    caplog.set_level(
        logging.WARNING,
        logger="googleapiclient.http",
    )
    caplog.clear()

    with pytest.raises(GoogleProviderError) as exc_info:
        request.execute(num_retries=0)

    assert exc_info.value.kind is GoogleProviderErrorKind.PERMISSION_REJECTED

    assert len(provider_calls) == 1

    provider_call = provider_calls[0]

    assert provider_call["retries"] is False
    assert provider_call["redirect"] is False

    logged_state = "\n".join(
        (record.getMessage() + "\n" + repr(record.__dict__)) for record in caplog.records
    )

    assert raw_marker not in logged_state
