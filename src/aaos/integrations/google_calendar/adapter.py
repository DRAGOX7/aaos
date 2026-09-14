from __future__ import annotations

import json
import re
from dataclasses import dataclass
from threading import Lock

from aaos.approval.adapters import ExternalAdapter
from aaos.approval.contracts import (
    ActionType,
    AdapterExecutionOutcome,
    AdapterOutcomeKind,
    AdapterReconciliationOutcome,
    ExecutableAction,
    OperationId,
    RemotePrecondition,
)

from .canonicalization import derive_provider_event_id
from .client import GoogleCalendarClient, ObservedGoogleEvent
from .contracts import CanonicalCalendarEvent, GoogleCalendarConfig, calendar_target
from .errors import GoogleProviderError, GoogleProviderErrorKind

_CREATE_EVENT = ActionType("CREATE_EVENT")
_DERIVATION_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EVENT_FIELDS = {"summary", "start", "end", "timezone", "description", "location"}
_PROVIDER = "google-calendar"
_ABSENT = "absent"

_DEFINITE_REJECTIONS = {
    GoogleProviderErrorKind.AUTHENTICATION_REJECTED,
    GoogleProviderErrorKind.PERMISSION_REJECTED,
    GoogleProviderErrorKind.INVALID_REQUEST,
}
_AMBIGUOUS_TRANSPORT_FAILURES = {
    GoogleProviderErrorKind.RATE_LIMITED,
    GoogleProviderErrorKind.SERVER_ERROR,
    GoogleProviderErrorKind.TIMEOUT,
    GoogleProviderErrorKind.TRANSPORT_ERROR,
}


@dataclass(frozen=True, slots=True)
class CalendarAdapterExecutionTrace:
    """Bounded test evidence; it contains no event or provider payload."""

    insert_count: int
    get_count: int
    last_calendar_id: str | None

    def __post_init__(self) -> None:
        for name in ("insert_count", "get_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.last_calendar_id is not None and not isinstance(self.last_calendar_id, str):
            raise TypeError("last_calendar_id must be a string or None")


def decode_calendar_action(action: ExecutableAction) -> CanonicalCalendarEvent:
    """Decode only the closed, canonical Phase 6B event argument contract."""

    if not isinstance(action, ExecutableAction):
        raise TypeError("action must be an ExecutableAction")
    if action.action_type != _CREATE_EVENT:
        raise ValueError("action type is not supported by the calendar adapter")
    try:
        parsed = json.loads(action.canonical_arguments.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("calendar arguments are not canonical JSON") from error
    if not isinstance(parsed, dict) or set(parsed) != _EVENT_FIELDS:
        raise ValueError("calendar arguments do not match the closed schema")
    summary = parsed["summary"]
    start = parsed["start"]
    end = parsed["end"]
    timezone_name = parsed["timezone"]
    description = parsed["description"]
    location = parsed["location"]
    if not all(isinstance(value, str) for value in (summary, start, end, timezone_name)):
        raise TypeError("required calendar arguments must be strings")
    if description is not None and not isinstance(description, str):
        raise TypeError("description must be a string or null")
    if location is not None and not isinstance(location, str):
        raise TypeError("location must be a string or null")
    return CanonicalCalendarEvent._create(
        summary,
        start,
        end,
        timezone_name,
        description,
        location,
    )


def _semantics_match(observed: ObservedGoogleEvent, expected: CanonicalCalendarEvent) -> bool:
    return (
        observed.summary == expected.summary
        and observed.start == expected.start
        and observed.end == expected.end
        and observed.timezone == expected.timezone
        and observed.description == expected.description
        and observed.location == expected.location
    )


def _unknown(*, transport: bool = False) -> AdapterExecutionOutcome:
    return AdapterExecutionOutcome(
        AdapterOutcomeKind.TRANSPORT_FAILURE if transport else AdapterOutcomeKind.UNKNOWN,
        "calendar provider outcome is unknown",
    )


def _reconciliation_unknown() -> AdapterReconciliationOutcome:
    return AdapterReconciliationOutcome(
        AdapterOutcomeKind.UNKNOWN,
        "calendar event outcome could not be reconciled",
    )


class GoogleCalendarAdapter(ExternalAdapter):
    """Least-authority Calendar adapter over the narrow provider client."""

    def __init__(
        self,
        client: GoogleCalendarClient,
        config: GoogleCalendarConfig,
        *,
        derivation_version: str,
    ) -> None:
        if not isinstance(config, GoogleCalendarConfig):
            raise TypeError("config must be a GoogleCalendarConfig")
        if not isinstance(derivation_version, str) or not _DERIVATION_VERSION.fullmatch(
            derivation_version
        ):
            raise ValueError("derivation_version is invalid or exceeds its limit")
        if not callable(getattr(client, "get_event", None)) or not callable(
            getattr(client, "insert_event", None)
        ):
            raise TypeError("client must implement the narrow GoogleCalendarClient protocol")
        self._client = client
        self._config = config
        self._derivation_version = derivation_version
        self._expected_events: dict[OperationId, CanonicalCalendarEvent] = {}
        self._insert_count = 0
        self._get_count = 0
        self._last_calendar_id: str | None = None
        self._lock = Lock()

    @property
    def trace(self) -> CalendarAdapterExecutionTrace:
        with self._lock:
            return CalendarAdapterExecutionTrace(
                self._insert_count,
                self._get_count,
                self._last_calendar_id,
            )

    def _binding_is_valid(self, action: ExecutableAction) -> bool:
        target = calendar_target(self._config)
        return (
            self._config.enabled
            and action.action_type == _CREATE_EVENT
            and action.target_identity == target
            and action.remote_precondition
            == RemotePrecondition(
                _PROVIDER,
                target.resource,
                self._derivation_version,
                _ABSENT,
            )
        )

    def _get_event(self, provider_event_id: str) -> ObservedGoogleEvent | None:
        with self._lock:
            self._get_count += 1
            self._last_calendar_id = self._config.calendar_id
        return self._client.get_event(self._config.calendar_id, provider_event_id)

    def _insert_event(
        self,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        with self._lock:
            self._insert_count += 1
            self._last_calendar_id = self._config.calendar_id
        return self._client.insert_event(self._config.calendar_id, event, provider_event_id)

    @staticmethod
    def _pre_dispatch_error(error: GoogleProviderError) -> AdapterExecutionOutcome:
        if error.kind in _DEFINITE_REJECTIONS:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar provider rejected the operation before dispatch",
            )
        return _unknown(transport=error.kind in _AMBIGUOUS_TRANSPORT_FAILURES)

    @staticmethod
    def _post_dispatch_error(error: GoogleProviderError) -> AdapterExecutionOutcome:
        if error.kind in _DEFINITE_REJECTIONS or error.kind is GoogleProviderErrorKind.NOT_FOUND:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar provider definitively rejected the insert",
            )
        if error.kind is GoogleProviderErrorKind.ALREADY_EXISTS:
            # The write was dispatched and another same-ID event now exists.
            # That is not Phase 6A NOT_DISPATCHED evidence: only read-only
            # reconciliation can determine the operation's durable outcome.
            return _unknown()
        return _unknown(transport=error.kind in _AMBIGUOUS_TRANSPORT_FAILURES)

    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        if not isinstance(action, ExecutableAction):
            raise TypeError("action must be an ExecutableAction")
        try:
            event = decode_calendar_action(action)
        except (TypeError, ValueError):
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar action projection was invalid",
            )
        if not self._binding_is_valid(action):
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar action binding was invalid",
            )
        provider_event_id = derive_provider_event_id(
            action.operation_id,
            derivation_version=self._derivation_version,
        )
        with self._lock:
            self._expected_events[action.operation_id] = event

        # This is the authoritative precondition read immediately before the
        # one permitted insert attempt. A prior advisory read has no authority.
        try:
            current = self._get_event(provider_event_id)
        except GoogleProviderError as error:
            return self._pre_dispatch_error(error)
        except Exception:
            # A provider implementation bug or untyped transport failure has
            # no safe evidence of non-acceptance and must remain unknown.
            return _unknown()
        if current is not None:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.PRECONDITION_STALE,
                "calendar event identity is no longer absent",
            )

        try:
            created = self._insert_event(event, provider_event_id)
        except GoogleProviderError as error:
            return self._post_dispatch_error(error)
        except Exception:
            return _unknown()
        if (
            not isinstance(created, ObservedGoogleEvent)
            or created.provider_event_id != provider_event_id
            or not _semantics_match(created, event)
        ):
            return _unknown()
        return AdapterExecutionOutcome(AdapterOutcomeKind.SUCCESS, "calendar event created")

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome:
        if not isinstance(operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId")
        with self._lock:
            expected = self._expected_events.get(operation_id)
        if expected is None:
            return _reconciliation_unknown()
        provider_event_id = derive_provider_event_id(
            operation_id,
            derivation_version=self._derivation_version,
        )
        try:
            observed = self._get_event(provider_event_id)
        except GoogleProviderError:
            return _reconciliation_unknown()
        except Exception:
            return _reconciliation_unknown()
        if (
            observed is not None
            and observed.provider_event_id == provider_event_id
            and _semantics_match(observed, expected)
        ):
            return AdapterReconciliationOutcome(
                AdapterOutcomeKind.SUCCESS,
                "calendar event exactly matched the approved operation",
            )
        return _reconciliation_unknown()


__all__ = [
    "CalendarAdapterExecutionTrace",
    "GoogleCalendarAdapter",
    "decode_calendar_action",
]
