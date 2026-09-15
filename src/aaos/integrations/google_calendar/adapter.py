from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

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
from .contracts import (
    CanonicalCalendarEvent,
    GoogleCalendarConfig,
    calendar_target,
)
from .errors import GoogleProviderError, GoogleProviderErrorKind

_CREATE_EVENT = ActionType("CREATE_EVENT")
_DERIVATION_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EVENT_FIELDS = {
    "summary",
    "start",
    "end",
    "timezone",
    "description",
    "location",
}
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


class ExpectedActionLoader(Protocol):
    """Read-only loader for the durable approved least-authority action."""

    def __call__(
        self,
        operation_id: OperationId,
        /,
    ) -> ExecutableAction | None: ...


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


def decode_calendar_action(
    action: ExecutableAction,
) -> CanonicalCalendarEvent:
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

    if not all(
        isinstance(value, str)
        for value in (
            summary,
            start,
            end,
            timezone_name,
        )
    ):
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


def _utc_instant(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value

    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("calendar instant must be timezone-aware")

    return parsed.astimezone(timezone.utc)


def _provider_instants_match(
    observed: str,
    expected: str,
) -> bool:
    # Canonical trusted DTOs may preserve the approved
    # fractional instant exactly. Exact equality must remain
    # valid before applying the narrower Google whole-second
    # read-back normalization.
    if observed == expected:
        return True

    try:
        observed_instant = _utc_instant(observed)
        expected_instant = _utc_instant(expected)
    except (TypeError, ValueError):
        return False

    # Live Google Calendar demonstrated whole-second response
    # precision. Accept only that exact provider representation;
    # do not introduce a fuzzy time tolerance.
    if observed_instant.microsecond != 0:
        return False

    return observed_instant == expected_instant.replace(microsecond=0)


def _semantics_match(
    observed: ObservedGoogleEvent,
    expected: CanonicalCalendarEvent,
) -> bool:
    return (
        observed.summary == expected.summary
        and _provider_instants_match(
            observed.start,
            expected.start,
        )
        and _provider_instants_match(
            observed.end,
            expected.end,
        )
        and observed.timezone == expected.timezone
        and observed.description == expected.description
        and observed.location == expected.location
    )


def _unknown(
    *,
    transport: bool = False,
) -> AdapterExecutionOutcome:
    return AdapterExecutionOutcome(
        (AdapterOutcomeKind.TRANSPORT_FAILURE if transport else AdapterOutcomeKind.UNKNOWN),
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
        expected_action_loader: ExpectedActionLoader,
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

        if not callable(expected_action_loader):
            raise TypeError("expected_action_loader must be callable")

        self._client = client
        self._config = config
        self._derivation_version = derivation_version
        self._expected_action_loader = expected_action_loader

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

    def _binding_is_valid(
        self,
        action: ExecutableAction,
    ) -> bool:
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

    def _get_event(
        self,
        provider_event_id: str,
    ) -> ObservedGoogleEvent | None:
        with self._lock:
            self._get_count += 1
            self._last_calendar_id = self._config.calendar_id

        return self._client.get_event(
            self._config.calendar_id,
            provider_event_id,
        )

    def _insert_event(
        self,
        event: CanonicalCalendarEvent,
        provider_event_id: str,
    ) -> ObservedGoogleEvent:
        with self._lock:
            self._insert_count += 1
            self._last_calendar_id = self._config.calendar_id

        return self._client.insert_event(
            self._config.calendar_id,
            event,
            provider_event_id,
        )

    @staticmethod
    def _pre_dispatch_error(
        error: GoogleProviderError,
    ) -> AdapterExecutionOutcome:
        if error.kind in _DEFINITE_REJECTIONS:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar provider rejected the operation before dispatch",
            )

        return _unknown(transport=(error.kind in _AMBIGUOUS_TRANSPORT_FAILURES))

    @staticmethod
    def _post_dispatch_error(
        error: GoogleProviderError,
    ) -> AdapterExecutionOutcome:
        if error.kind in _DEFINITE_REJECTIONS or error.kind is GoogleProviderErrorKind.NOT_FOUND:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.AUTHORITATIVE_FAILURE,
                "calendar provider definitively rejected the insert",
            )

        if error.kind is GoogleProviderErrorKind.ALREADY_EXISTS:
            # The write was dispatched and another same-ID event now
            # exists. This is not proof that our write failed. Only
            # read-only reconciliation may determine the final outcome.
            return _unknown()

        return _unknown(transport=(error.kind in _AMBIGUOUS_TRANSPORT_FAILURES))

    def execute(
        self,
        action: ExecutableAction,
    ) -> AdapterExecutionOutcome:
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

        # Authoritative provider-side precondition immediately before
        # the one permitted mutation attempt.
        try:
            current = self._get_event(provider_event_id)
        except GoogleProviderError as error:
            return self._pre_dispatch_error(error)
        except Exception:
            # Untyped provider/client failure gives no trustworthy
            # evidence that execution can proceed.
            return _unknown()

        if current is not None:
            return AdapterExecutionOutcome(
                AdapterOutcomeKind.PRECONDITION_STALE,
                "calendar event identity is no longer absent",
            )

        try:
            created = self._insert_event(
                event,
                provider_event_id,
            )
        except GoogleProviderError as error:
            return self._post_dispatch_error(error)
        except Exception:
            return _unknown()

        if (
            not isinstance(
                created,
                ObservedGoogleEvent,
            )
            or created.provider_event_id != provider_event_id
            or not _semantics_match(
                created,
                event,
            )
        ):
            return _unknown()

        return AdapterExecutionOutcome(
            AdapterOutcomeKind.SUCCESS,
            "calendar event created",
        )

    def reconcile(
        self,
        operation_id: OperationId,
    ) -> AdapterReconciliationOutcome:
        if not isinstance(operation_id, OperationId):
            raise TypeError("operation_id must be an OperationId")

        # Reload the approved least-authority projection from durable
        # AAOS state. Reconciliation must not depend on process-local
        # memory from execute().
        try:
            expected_action = self._expected_action_loader(operation_id)
        except Exception:
            return _reconciliation_unknown()

        if (
            expected_action is None
            or not isinstance(
                expected_action,
                ExecutableAction,
            )
            or expected_action.operation_id != operation_id
        ):
            return _reconciliation_unknown()

        try:
            expected = decode_calendar_action(expected_action)
        except (TypeError, ValueError):
            return _reconciliation_unknown()

        # Re-validate the trusted target/precondition binding before
        # consulting provider state.
        if not self._binding_is_valid(expected_action):
            return _reconciliation_unknown()

        provider_event_id = derive_provider_event_id(
            operation_id,
            derivation_version=self._derivation_version,
        )

        # Reconciliation is mechanically read-only.
        try:
            observed = self._get_event(provider_event_id)
        except GoogleProviderError:
            return _reconciliation_unknown()
        except Exception:
            return _reconciliation_unknown()

        if (
            observed is not None
            and observed.provider_event_id == provider_event_id
            and _semantics_match(
                observed,
                expected,
            )
        ):
            return AdapterReconciliationOutcome(
                AdapterOutcomeKind.SUCCESS,
                "calendar event exactly matched the approved operation",
            )

        return _reconciliation_unknown()


__all__ = [
    "CalendarAdapterExecutionTrace",
    "ExpectedActionLoader",
    "GoogleCalendarAdapter",
    "decode_calendar_action",
]
