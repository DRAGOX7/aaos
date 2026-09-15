from __future__ import annotations

import importlib
import json
import re
import socket
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Protocol, cast

from .contracts import CanonicalCalendarEvent, GoogleCalendarConfig
from .errors import GoogleProviderError, GoogleProviderErrorKind

_PROVIDER_EVENT_ID = re.compile(r"^[0-9a-v]{5,1024}$")
_RAW_PROVIDER_RFC3339_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"T\d{2}:\d{2}:\d{2}"
    r"(?:\.(?P<fraction>\d{1,9}))?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)
_PROJECTION_FIELDS = {
    "provider_event_id",
    "summary",
    "start",
    "end",
    "timezone",
    "description",
    "location",
}
_PROJECTION_REQUIRED_FIELDS = {
    "provider_event_id",
    "summary",
    "start",
    "end",
    "timezone",
}

_FACTORY_CONSTRUCTION_TOKEN = object()

_MALFORMED_RESPONSE_REASON = "provider response did not match the approved event projection"
_FAILURE_REASONS = {
    GoogleProviderErrorKind.AUTHENTICATION_REJECTED: ("provider authentication was rejected"),
    GoogleProviderErrorKind.PERMISSION_REJECTED: ("provider permission was rejected"),
    GoogleProviderErrorKind.INVALID_REQUEST: "provider rejected the request",
    GoogleProviderErrorKind.NOT_FOUND: "provider event was not found",
    GoogleProviderErrorKind.ALREADY_EXISTS: "provider event already exists",
    GoogleProviderErrorKind.RATE_LIMITED: "provider rate limit was reached",
    GoogleProviderErrorKind.SERVER_ERROR: "provider server failed",
    GoogleProviderErrorKind.TIMEOUT: "provider request timed out",
    GoogleProviderErrorKind.TRANSPORT_ERROR: "provider transport failed",
    GoogleProviderErrorKind.MALFORMED_RESPONSE: _MALFORMED_RESPONSE_REASON,
}

REAL_ADAPTER_UNAVAILABLE = "REAL_ADAPTER_UNAVAILABLE"
_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_SUPPORTED_ADAPTER_VERSION = "calendar-adapter-v1"

_GET_TIMEOUT_SECONDS = 5.0
_INSERT_TIMEOUT_SECONDS = 10.0
_GET_MAX_ATTEMPTS = 2
_GET_RETRY_BACKOFF_SECONDS = 0.05

_CREDENTIAL_SIZE_LIMIT = 128 * 1024

_TRANSIENT_GET_ERRORS = {
    GoogleProviderErrorKind.RATE_LIMITED,
    GoogleProviderErrorKind.SERVER_ERROR,
    GoogleProviderErrorKind.TIMEOUT,
    GoogleProviderErrorKind.TRANSPORT_ERROR,
}

_RATE_LIMIT_REASONS = frozenset(
    {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "quotaExceeded",
    }
)

_PROVIDER_ERROR_CONTENT_LIMIT = 16 * 1024
_PROVIDER_ERROR_ENTRY_LIMIT = 16
_PROVIDER_ERROR_REASON_LIMIT = 128


def _validate_provider_event_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("provider_event_id must be a string")
    if not _PROVIDER_EVENT_ID.fullmatch(value):
        raise ValueError("provider_event_id is invalid or exceeds its limit")
    return value


def _validate_calendar_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("calendar_id must be a string")
    if (
        not value
        or len(value) > 4096
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise ValueError("calendar_id is invalid or exceeds its limit")
    return value


def _validate_trusted_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    if (
        not value
        or len(value) > 4096
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise ValueError
    return value


def _unavailable() -> RuntimeError:
    return RuntimeError(REAL_ADAPTER_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class ObservedGoogleEvent:
    provider_event_id: str
    summary: str
    start: str
    end: str
    timezone: str
    description: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        _validate_provider_event_id(self.provider_event_id)
        CanonicalCalendarEvent._create(
            self.summary,
            self.start,
            self.end,
            self.timezone,
            self.description,
            self.location,
        )


class GoogleCalendarClient(Protocol):
    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> ObservedGoogleEvent | None: ...

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent: ...


class GoogleSdkFacade(Protocol):
    """Least-authority SDK seam: exactly the two approved Calendar operations."""

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> object | None: ...

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> object: ...


class _GoogleRequest(Protocol):
    def execute(self, *, num_retries: int = 0) -> object: ...


class _GoogleEventsResource(Protocol):
    def get(
        self,
        *,
        calendarId: str,
        eventId: str,
    ) -> _GoogleRequest: ...

    def insert(
        self,
        *,
        calendarId: str,
        body: Mapping[str, object],
    ) -> _GoogleRequest: ...


class _GoogleService(Protocol):
    def events(self) -> _GoogleEventsResource: ...


class _Urllib3Response(Protocol):
    @property
    def status(self) -> int: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def data(self) -> bytes: ...


class _Urllib3Http(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: object = None,
        fields: object = None,
        headers: Mapping[str, str] | None = None,
        json: object = None,
        **kwargs: object,
    ) -> _Urllib3Response: ...


class _CredentialRefreshRequest:
    """Private non-logging transport used only for credential refresh."""

    def __init__(
        self,
        http: _Urllib3Http,
    ) -> None:
        self._http = http

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: object = None,
        headers: Mapping[str, str] | None = None,
        timeout: object = None,
        **_: object,
    ) -> _Urllib3Response:
        # The pool already owns the trusted finite timeout. Do not permit
        # Google Auth to widen it through its default timeout argument.
        del timeout

        return self._http.request(
            method,
            url,
            body=body,
            headers=headers,
            retries=False,
            redirect=False,
        )


class _GoogleCredential(Protocol):
    @property
    def service_account_email(self) -> str: ...

    @property
    def scopes(self) -> Sequence[str] | None: ...

    def before_request(
        self,
        request: _CredentialRefreshRequest,
        method: str,
        url: str,
        headers: MutableMapping[str, str],
    ) -> None: ...


class _GoogleApiCompatResponse(Mapping[str, str]):
    """Minimal httplib2-compatible response projection for googleapiclient."""

    def __init__(
        self,
        *,
        status: int,
        reason: str | None,
        headers: Mapping[str, str],
    ) -> None:
        self.status = status
        self.reason = reason
        self._headers = {str(key).lower(): str(value) for key, value in headers.items()}

    def __getitem__(self, key: str) -> str:
        return self._headers[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._headers)

    def __len__(self) -> int:
        return len(self._headers)


class _GoogleApiCompatHttp:
    """Authenticated googleapiclient seam with logging/replay disabled."""

    def __init__(
        self,
        credentials: _GoogleCredential,
        http: _Urllib3Http,
    ) -> None:
        self._credentials = credentials
        self._http = http
        self._refresh_request = _CredentialRefreshRequest(http)

    def request(
        self,
        uri: str,
        method: str = "GET",
        body: object = None,
        headers: Mapping[str, str] | None = None,
        **_: object,
    ) -> tuple[_GoogleApiCompatResponse, bytes]:
        request_headers: MutableMapping[str, str] = dict(headers or {})

        self._credentials.before_request(
            self._refresh_request,
            method,
            uri,
            request_headers,
        )

        response = self._http.request(
            method,
            uri,
            body=body,
            headers=request_headers,
            retries=False,
            redirect=False,
        )

        status = response.status
        reason = response.reason
        response_headers = response.headers
        content = response.data

        if not isinstance(status, int):
            raise TypeError

        if reason is not None and not isinstance(reason, str):
            raise TypeError

        if not isinstance(response_headers, Mapping):
            raise TypeError

        if not isinstance(content, bytes):
            raise TypeError

        if not 200 <= status <= 299:
            kind = _provider_error_kind_from_status(
                status,
                reasons=_google_error_reasons_from_content(content),
            )
            raise GoogleProviderError(
                kind,
                _FAILURE_REASONS[kind],
            )

        projected_headers: dict[str, str] = {}

        for key, value in response_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError

            projected_headers[key] = value

        return (
            _GoogleApiCompatResponse(
                status=status,
                reason=reason,
                headers=projected_headers,
            ),
            content,
        )


def _projection_value(
    raw: Mapping[object, object],
    name: str,
) -> object:
    if name in raw:
        return raw[name]
    return None


def _project_observed_event(raw: object) -> ObservedGoogleEvent:
    if not isinstance(raw, Mapping):
        raise TypeError

    keys = set(raw)

    if not all(isinstance(key, str) for key in keys):
        raise TypeError

    if not _PROJECTION_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(_PROJECTION_FIELDS):
        raise ValueError

    provider_event_id = _projection_value(raw, "provider_event_id")
    summary = _projection_value(raw, "summary")
    start = _projection_value(raw, "start")
    end = _projection_value(raw, "end")
    timezone_name = _projection_value(raw, "timezone")
    description = _projection_value(raw, "description")
    location = _projection_value(raw, "location")

    if not isinstance(provider_event_id, str):
        raise TypeError
    if not isinstance(summary, str):
        raise TypeError
    if not isinstance(start, str):
        raise TypeError
    if not isinstance(end, str):
        raise TypeError
    if not isinstance(timezone_name, str):
        raise TypeError
    if description is not None and not isinstance(description, str):
        raise TypeError
    if location is not None and not isinstance(location, str):
        raise TypeError

    return ObservedGoogleEvent(
        provider_event_id=provider_event_id,
        summary=summary,
        start=start,
        end=end,
        timezone=timezone_name,
        description=description,
        location=location,
    )


def project_observed_event(raw: object) -> ObservedGoogleEvent:
    """Project an untrusted provider-shaped value into the closed observation DTO."""

    projection: ObservedGoogleEvent | None = None
    malformed = False

    try:
        projection = _project_observed_event(raw)
    except Exception:
        malformed = True

    if malformed:
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        ) from None

    assert projection is not None
    return projection


def _http_status(error: Exception) -> int | None:
    response = getattr(error, "resp", None)
    status = getattr(response, "status", None)

    return status if isinstance(status, int) else None


def _google_error_reasons_from_content(
    content: object,
) -> frozenset[str]:
    if not isinstance(content, bytes):
        return frozenset()

    if len(content) > _PROVIDER_ERROR_CONTENT_LIMIT:
        return frozenset()

    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return frozenset()

    if not isinstance(parsed, Mapping):
        return frozenset()

    error_payload = parsed.get("error")
    if not isinstance(error_payload, Mapping):
        return frozenset()

    errors = error_payload.get("errors")
    if not isinstance(errors, list):
        return frozenset()

    reasons: set[str] = set()

    for item in errors[:_PROVIDER_ERROR_ENTRY_LIMIT]:
        if not isinstance(item, Mapping):
            continue

        reason = item.get("reason")

        if isinstance(reason, str) and reason and len(reason) <= _PROVIDER_ERROR_REASON_LIMIT:
            reasons.add(reason)

    return frozenset(reasons)


def _google_error_reasons(
    error: Exception,
) -> frozenset[str]:
    return _google_error_reasons_from_content(
        getattr(error, "content", None),
    )


def _provider_error_kind_from_status(
    status: int | None,
    *,
    reasons: frozenset[str] = frozenset(),
) -> GoogleProviderErrorKind:
    if status == 401:
        return GoogleProviderErrorKind.AUTHENTICATION_REJECTED

    if status == 403:
        if reasons & _RATE_LIMIT_REASONS:
            return GoogleProviderErrorKind.RATE_LIMITED

        return GoogleProviderErrorKind.PERMISSION_REJECTED

    if status == 404:
        return GoogleProviderErrorKind.NOT_FOUND

    if status == 408:
        return GoogleProviderErrorKind.TIMEOUT

    if status == 409:
        return GoogleProviderErrorKind.ALREADY_EXISTS

    if status == 429:
        return GoogleProviderErrorKind.RATE_LIMITED

    if status is not None and 500 <= status <= 599:
        return GoogleProviderErrorKind.SERVER_ERROR

    if status is not None and 400 <= status <= 499:
        return GoogleProviderErrorKind.INVALID_REQUEST

    return GoogleProviderErrorKind.TRANSPORT_ERROR


def _provider_error_from_exception(
    error: Exception,
) -> GoogleProviderError:
    if isinstance(error, GoogleProviderError):
        return error

    if isinstance(error, (TimeoutError, socket.timeout)):
        kind = GoogleProviderErrorKind.TIMEOUT
    else:
        kind = _provider_error_kind_from_status(
            _http_status(error),
            reasons=_google_error_reasons(error),
        )

    return GoogleProviderError(
        kind,
        _FAILURE_REASONS[kind],
    )


def _canonical_provider_instant(value: object) -> str:
    if not isinstance(value, str):
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        )

    # Establish the raw provider representation before parsing.
    # datetime.fromisoformat() must never be allowed to discard
    # provider precision first.
    match = _RAW_PROVIDER_RFC3339_INSTANT.fullmatch(value)

    if match is None:
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        )

    fraction = match.group("fraction")

    if fraction is not None and any(digit != "0" for digit in fraction):
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        )

    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        )

    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _google_projection(raw: object) -> Mapping[str, object]:
    try:
        if not isinstance(raw, Mapping):
            raise TypeError

        provider_event_id = raw["id"]
        summary = raw["summary"]
        start_raw = raw["start"]
        end_raw = raw["end"]

        if not isinstance(provider_event_id, str):
            raise TypeError

        if not isinstance(summary, str):
            raise TypeError

        if not isinstance(start_raw, Mapping):
            raise TypeError

        if not isinstance(end_raw, Mapping):
            raise TypeError

        start = _canonical_provider_instant(start_raw.get("dateTime"))
        end = _canonical_provider_instant(end_raw.get("dateTime"))

        start_timezone = start_raw.get("timeZone")
        end_timezone = end_raw.get("timeZone")

        if (
            not isinstance(start_timezone, str)
            or not isinstance(end_timezone, str)
            or start_timezone != end_timezone
        ):
            raise TypeError

        description = raw.get("description")
        location = raw.get("location")

        if description is not None and not isinstance(description, str):
            raise TypeError

        if location is not None and not isinstance(location, str):
            raise TypeError

        return {
            "provider_event_id": provider_event_id,
            "summary": summary,
            "start": start,
            "end": end,
            "timezone": start_timezone,
            "description": description,
            "location": location,
        }

    except GoogleProviderError:
        raise

    except Exception:
        raise GoogleProviderError(
            GoogleProviderErrorKind.MALFORMED_RESPONSE,
            _MALFORMED_RESPONSE_REASON,
        ) from None


class _OfficialGoogleSdkFacade:
    def __init__(
        self,
        *,
        get_events: _GoogleEventsResource,
        insert_events: _GoogleEventsResource,
    ) -> None:
        self._get_events = get_events
        self._insert_events = insert_events

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> object | None:
        try:
            raw = self._get_events.get(
                calendarId=calendar_id,
                eventId=provider_event_id,
            ).execute(num_retries=0)

        except GoogleProviderError as error:
            if error.kind is GoogleProviderErrorKind.NOT_FOUND:
                return None

            raise

        except Exception as error:
            if _http_status(error) == 404:
                return None

            raise _provider_error_from_exception(error) from None

        return _google_projection(raw)

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> object:
        body: dict[str, object] = {
            "id": provider_event_id,
            "summary": event.summary,
            "start": {
                "dateTime": event.start,
                "timeZone": event.timezone,
            },
            "end": {
                "dateTime": event.end,
                "timeZone": event.timezone,
            },
        }

        if event.description is not None:
            body["description"] = event.description

        if event.location is not None:
            body["location"] = event.location

        try:
            raw = self._insert_events.insert(
                calendarId=calendar_id,
                body=body,
            ).execute(num_retries=0)

        except Exception as error:
            raise _provider_error_from_exception(error) from None

        return _google_projection(raw)


class RealGoogleCalendarClient:
    """Calendar client bound to one trusted calendar and one narrow SDK facade."""

    def __init__(
        self,
        sdk: GoogleSdkFacade,
        calendar_id: str,
        *,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _FACTORY_CONSTRUCTION_TOKEN:
            raise _unavailable()

        self._sdk = sdk
        self._calendar_id = calendar_id

    def _require_calendar(
        self,
        calendar_id: str,
    ) -> str:
        try:
            selected = _validate_calendar_id(calendar_id)
        except (TypeError, ValueError):
            raise _unavailable() from None

        if selected != self._calendar_id:
            raise _unavailable()

        return selected

    @staticmethod
    def _project_result(
        raw: object,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        if isinstance(raw, ObservedGoogleEvent):
            observed = raw
        else:
            observed = project_observed_event(raw)

        if observed.provider_event_id != provider_event_id:
            raise GoogleProviderError(
                GoogleProviderErrorKind.MALFORMED_RESPONSE,
                _MALFORMED_RESPONSE_REASON,
            )

        return observed

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> ObservedGoogleEvent | None:
        trusted_calendar_id = self._require_calendar(calendar_id)
        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)

        for attempt in range(_GET_MAX_ATTEMPTS):
            try:
                raw = self._sdk.get_event(
                    trusted_calendar_id,
                    trusted_provider_event_id,
                )

            except Exception as error:
                provider_error = _provider_error_from_exception(error)

                if provider_error.kind in _TRANSIENT_GET_ERRORS and attempt + 1 < _GET_MAX_ATTEMPTS:
                    time.sleep(_GET_RETRY_BACKOFF_SECONDS)
                    continue

                raise provider_error from None

            if raw is None:
                return None

            return self._project_result(
                raw,
                trusted_provider_event_id,
            )

        raise AssertionError("bounded get retry loop exhausted unexpectedly")

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        trusted_calendar_id = self._require_calendar(calendar_id)
        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)

        if not isinstance(event, CanonicalCalendarEvent):
            raise TypeError("event must be a CanonicalCalendarEvent")

        event._validate()

        try:
            raw = self._sdk.insert_event(
                trusted_calendar_id,
                event,
                trusted_provider_event_id,
            )

        except Exception as error:
            raise _provider_error_from_exception(error) from None

        return self._project_result(
            raw,
            trusted_provider_event_id,
        )


def _validate_factory_config(
    config: GoogleCalendarConfig,
) -> None:
    if not isinstance(config, GoogleCalendarConfig):
        raise _unavailable()

    try:
        _validate_calendar_id(config.calendar_id)
        _validate_trusted_text(config.expected_service_identity)
        _validate_trusted_text(config.credential_path)
        _validate_trusted_text(config.provider_adapter_version)

    except (TypeError, ValueError):
        raise _unavailable() from None

    if not config.enabled:
        raise _unavailable()

    if config.oauth_scope != _EVENTS_SCOPE:
        raise _unavailable()

    if config.provider_adapter_version != _SUPPORTED_ADAPTER_VERSION:
        raise _unavailable()


def _load_credential_info(
    config: GoogleCalendarConfig,
) -> dict[str, object]:
    try:
        path = Path(config.credential_path)

        if not path.is_absolute():
            raise ValueError

        resolved = path.resolve(strict=True)

        repository_root = Path(__file__).resolve().parents[4]

        if resolved.is_relative_to(repository_root):
            raise ValueError

        if not resolved.is_file() or resolved.stat().st_size > _CREDENTIAL_SIZE_LIMIT:
            raise ValueError

        raw = resolved.read_bytes()

        parsed: object = json.loads(raw.decode("utf-8"))

        if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
            raise ValueError

        credential_type = parsed.get("type")
        client_email = parsed.get("client_email")

        if credential_type != "service_account":
            raise ValueError

        if client_email != config.expected_service_identity:
            raise ValueError

        return cast(
            dict[str, object],
            parsed,
        )

    except Exception:
        raise _unavailable() from None


def _build_official_sdk(
    config: GoogleCalendarConfig,
    credential_info: Mapping[str, object],
) -> GoogleSdkFacade:
    try:
        urllib3_module = importlib.import_module("urllib3")

        service_account_module = importlib.import_module("google.oauth2.service_account")

        discovery_module = importlib.import_module("googleapiclient.discovery")

        credentials_type = getattr(
            service_account_module,
            "Credentials",
        )

        credential_factory = cast(
            Callable[..., _GoogleCredential],
            getattr(
                credentials_type,
                "from_service_account_info",
            ),
        )

        pool_manager_factory = cast(
            Callable[..., object],
            getattr(
                urllib3_module,
                "PoolManager",
            ),
        )

        service_builder = cast(
            Callable[..., object],
            getattr(
                discovery_module,
                "build",
            ),
        )

        credentials = credential_factory(
            dict(credential_info),
            scopes=[config.oauth_scope],
        )

        if credentials.service_account_email != config.expected_service_identity:
            raise ValueError

        if tuple(credentials.scopes or ()) != (config.oauth_scope,):
            raise ValueError

        get_pool = pool_manager_factory(
            timeout=_GET_TIMEOUT_SECONDS,
            retries=False,
        )

        insert_pool = pool_manager_factory(
            timeout=_INSERT_TIMEOUT_SECONDS,
            retries=False,
        )

        get_http = _GoogleApiCompatHttp(
            credentials,
            cast(_Urllib3Http, get_pool),
        )

        insert_http = _GoogleApiCompatHttp(
            credentials,
            cast(_Urllib3Http, insert_pool),
        )

        get_service = cast(
            _GoogleService,
            service_builder(
                "calendar",
                "v3",
                http=get_http,
                cache_discovery=False,
            ),
        )

        insert_service = cast(
            _GoogleService,
            service_builder(
                "calendar",
                "v3",
                http=insert_http,
                cache_discovery=False,
            ),
        )

        return _OfficialGoogleSdkFacade(
            get_events=get_service.events(),
            insert_events=insert_service.events(),
        )

    except Exception:
        raise _unavailable() from None


class GoogleCalendarClientFactory:
    """Explicit fail-closed factory for the real Calendar integration."""

    @staticmethod
    def build(
        config: GoogleCalendarConfig,
        *,
        sdk: GoogleSdkFacade | None = None,
    ) -> GoogleCalendarClient:
        _validate_factory_config(config)

        if sdk is not None:
            if not callable(
                getattr(
                    sdk,
                    "get_event",
                    None,
                )
            ) or not callable(
                getattr(
                    sdk,
                    "insert_event",
                    None,
                )
            ):
                raise _unavailable()

            return RealGoogleCalendarClient(
                sdk,
                config.calendar_id,
                _construction_token=(_FACTORY_CONSTRUCTION_TOKEN),
            )

        credential_info = _load_credential_info(config)

        real_sdk = _build_official_sdk(
            config,
            credential_info,
        )

        return RealGoogleCalendarClient(
            real_sdk,
            config.calendar_id,
            _construction_token=(_FACTORY_CONSTRUCTION_TOKEN),
        )


class FakeGoogleCalendarClient:
    """Deterministic bounded in-memory implementation of GoogleCalendarClient."""

    def __init__(
        self,
        *,
        initial_events: tuple[
            tuple[str, ObservedGoogleEvent],
            ...,
        ] = (),
        get_error: GoogleProviderErrorKind | None = None,
        insert_error: GoogleProviderErrorKind | None = None,
    ) -> None:
        if not isinstance(initial_events, tuple):
            raise TypeError("initial_events must be a tuple")

        if get_error is not None and not isinstance(
            get_error,
            GoogleProviderErrorKind,
        ):
            raise TypeError("get_error must be a GoogleProviderErrorKind")

        if insert_error is not None and not isinstance(
            insert_error,
            GoogleProviderErrorKind,
        ):
            raise TypeError("insert_error must be a GoogleProviderErrorKind")

        events: dict[
            tuple[str, str],
            ObservedGoogleEvent,
        ] = {}

        for seed in initial_events:
            if not isinstance(seed, tuple) or len(seed) != 2:
                raise TypeError("each initial event must be a calendar/event tuple")

            calendar_id, event = seed

            _validate_calendar_id(calendar_id)

            if not isinstance(
                event,
                ObservedGoogleEvent,
            ):
                raise TypeError("initial event must be an ObservedGoogleEvent")

            key = (
                calendar_id,
                event.provider_event_id,
            )

            if key in events:
                raise ValueError("initial events contain a duplicate provider event ID")

            events[key] = event

        self._events = events
        self._get_error = get_error
        self._insert_error = insert_error
        self._get_count = 0
        self._insert_count = 0
        self._lock = Lock()

    @property
    def get_count(self) -> int:
        with self._lock:
            return self._get_count

    @property
    def insert_count(self) -> int:
        with self._lock:
            return self._insert_count

    @property
    def events(
        self,
    ) -> tuple[ObservedGoogleEvent, ...]:
        with self._lock:
            return tuple(event for _, event in sorted(self._events.items()))

    def get_event(
        self,
        calendar_id: str,
        provider_event_id: str,
    ) -> ObservedGoogleEvent | None:
        trusted_calendar_id = _validate_calendar_id(calendar_id)

        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)

        with self._lock:
            self._get_count += 1

            if self._get_error is not None:
                raise GoogleProviderError(
                    self._get_error,
                    _FAILURE_REASONS[self._get_error],
                )

            return self._events.get(
                (
                    trusted_calendar_id,
                    trusted_provider_event_id,
                )
            )

    def insert_event(
        self,
        calendar_id: str,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        trusted_calendar_id = _validate_calendar_id(calendar_id)

        trusted_provider_event_id = _validate_provider_event_id(provider_event_id)

        if not isinstance(
            event,
            CanonicalCalendarEvent,
        ):
            raise TypeError("event must be a CanonicalCalendarEvent")

        event._validate()

        observed = ObservedGoogleEvent(
            provider_event_id=(trusted_provider_event_id),
            summary=event.summary,
            start=event.start,
            end=event.end,
            timezone=event.timezone,
            description=event.description,
            location=event.location,
        )

        key = (
            trusted_calendar_id,
            trusted_provider_event_id,
        )

        with self._lock:
            self._insert_count += 1

            if self._insert_error is not None:
                raise GoogleProviderError(
                    self._insert_error,
                    _FAILURE_REASONS[self._insert_error],
                )

            if key in self._events:
                raise GoogleProviderError(
                    GoogleProviderErrorKind.ALREADY_EXISTS,
                    _FAILURE_REASONS[GoogleProviderErrorKind.ALREADY_EXISTS],
                )

            self._events[key] = observed

            return observed


__all__ = [
    "FakeGoogleCalendarClient",
    "GoogleCalendarClient",
    "GoogleCalendarClientFactory",
    "GoogleSdkFacade",
    "ObservedGoogleEvent",
    "REAL_ADAPTER_UNAVAILABLE",
    "RealGoogleCalendarClient",
    "project_observed_event",
]
