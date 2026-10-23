"""Production composition of the frozen Calendar parts (Phase 12, slice 12B).

Builds, for the served app, what ``tests/phase6b_evaluation/real_acceptance_cli.py``
built for the one-off 6B acceptance run, without its acceptance-only parts. Nothing
frozen changes: the Phase 6A approval core, the 6B client factory and adapter, and the
Phase 7E task service and executor are used as they are.

* ``CREATE_EVENT`` only, through the closed six-field canonical schema;
* the tool and policy fingerprints are computed from their contracts, not placeholders;
* the client comes from ``GoogleCalendarClientFactory`` and never falls back to the fake;
* a proposal waits up to 24 hours for approval, and its action is valid for 25 hours
  (owner decision D1), passed as parameters to the frozen task service.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from aaos.agent.contracts import JSONValue, PermissionClass
from aaos.agent.policy import PermissionPolicy
from aaos.approval.adapters import load_executable_action
from aaos.approval.contracts import (
    ActionType,
    ExecutableAction,
    OperationId,
    Phase6ActionDefinition,
    TrustedApproverContext,
)
from aaos.approval.repository import ApprovalRepository
from aaos.tasks.execution import TaskExecutionCoordinator
from aaos.tasks.read_service import TaskReadService
from aaos.tasks.repository import TaskRepository
from aaos.tasks.service import TaskApplicationService

from .adapter import GoogleCalendarAdapter
from .canonicalization import canonical_event_arguments
from .client import GoogleCalendarClient, GoogleCalendarClientFactory
from .contracts import CanonicalCalendarEvent, GoogleCalendarConfig
from .setup import PROVIDER_ADAPTER_VERSION, CalendarProposalsSetup

APPROVAL_LIFETIME = timedelta(hours=24)
ACTION_LIFETIME = timedelta(hours=25)
# The frozen 7E task service stamps each action's remote precondition with the config's
# provider_adapter_version, and the frozen 6B adapter accepts only an action whose
# precondition carries its derivation_version. They must therefore be the same value, or
# every approved action is refused before Google is contacted. (The 6B acceptance tool used
# "calendar-provider-id-v1" but bypassed the 7E task service.) The value also names the
# namespace of the event IDs AAOS derives.
CALENDAR_DERIVATION_VERSION = PROVIDER_ADAPTER_VERSION

_ARGUMENTS = ("summary", "start", "end", "timezone", "description", "location")
_POLICY_VERSION = "phase5-v1"
_TOOL_CONTRACT: Mapping[str, object] = {
    "action_type": "CREATE_EVENT",
    "tool_name": "calendar_create_event",
    "tool_schema_version": "phase6b-calendar-v1",
    "required_permission": PermissionClass.LOCAL_STATE_WRITE.value,
    "timeout_seconds": 10,
    "arguments": {
        "summary": "string",
        "start": "rfc3339",
        "end": "rfc3339",
        "timezone": "iana",
        "description": "string|null",
        "location": "string|null",
    },
}
_POLICY_CONTRACT: Mapping[str, object] = {
    "policy_version": _POLICY_VERSION,
    "granted_permissions": [PermissionClass.LOCAL_STATE_WRITE.value],
}

ClientBuilder = Callable[[GoogleCalendarConfig], GoogleCalendarClient]


class CalendarProposalsUnavailable(RuntimeError):
    """Calendar proposals can't be built; the reason is deliberately not shown."""

    def __init__(self) -> None:
        super().__init__("Calendar proposals are unavailable")


def _fingerprint(document: Mapping[str, object]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate(arguments: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
    """The closed 6B schema: exactly six fields, then the canonical event."""
    if set(arguments) != set(_ARGUMENTS):
        raise ValueError("calendar arguments do not match the closed schema")
    for name in ("summary", "start", "end", "timezone"):
        if not isinstance(arguments[name], str):
            raise TypeError(f"{name} must be a string")
    for name in ("description", "location"):
        if arguments[name] is not None and not isinstance(arguments[name], str):
            raise TypeError(f"{name} must be a string or null")
    canonical = CanonicalCalendarEvent._create(
        arguments["summary"],  # type: ignore[arg-type]
        arguments["start"],  # type: ignore[arg-type]
        arguments["end"],  # type: ignore[arg-type]
        arguments["timezone"],  # type: ignore[arg-type]
        arguments["description"],  # type: ignore[arg-type]
        arguments["location"],  # type: ignore[arg-type]
    )
    return canonical_event_arguments(canonical)


def calendar_action_definition() -> Phase6ActionDefinition:
    """The ``CREATE_EVENT`` definition, with fingerprints computed from its contracts."""
    return Phase6ActionDefinition(
        action_type=ActionType("CREATE_EVENT"),
        tool_name="calendar_create_event",
        tool_schema_version="phase6b-calendar-v1",
        tool_definition_fingerprint=_fingerprint(_TOOL_CONTRACT),
        required_permission=PermissionClass.LOCAL_STATE_WRITE,
        policy_version=_POLICY_VERSION,
        policy_fingerprint=_fingerprint(_POLICY_CONTRACT),
        timeout_seconds=10,
        argument_validator=_validate,
    )


def calendar_capability_policy() -> PermissionPolicy:
    return PermissionPolicy.from_values((PermissionClass.LOCAL_STATE_WRITE,))


@dataclass(frozen=True, slots=True)
class CalendarServices:
    """The proposal service and the executor the served app is given (no values in repr)."""

    proposals: TaskApplicationService = field(repr=False)
    executor: TaskExecutionCoordinator = field(repr=False)
    approver: TrustedApproverContext = field(repr=False)


def build_calendar_services(
    sessions: sessionmaker[Session],
    setup: CalendarProposalsSetup,
    *,
    client_builder: ClientBuilder | None = None,
) -> CalendarServices:
    """Compose the frozen parts for an ``ON`` setup; anything else is refused."""
    if setup.state != "ON" or setup.config is None or setup.approver is None:
        raise CalendarProposalsUnavailable()
    config = setup.config
    build = client_builder or GoogleCalendarClientFactory.build
    try:
        client = build(config)
    except Exception:
        raise CalendarProposalsUnavailable() from None

    approvals = ApprovalRepository(sessions)

    def load_expected(operation_id: OperationId) -> ExecutableAction:
        return load_executable_action(ApprovalRepository(sessions), operation_id)

    adapter = GoogleCalendarAdapter(
        client,
        config,
        derivation_version=CALENDAR_DERIVATION_VERSION,
        expected_action_loader=load_expected,
    )
    definition = calendar_action_definition()
    policy = calendar_capability_policy()
    proposals = TaskApplicationService(
        TaskRepository(sessions),
        approvals,
        definition=definition,
        capability_policy=policy,
        actor_identity=setup.approver.approver_identity,
        approver=setup.approver,
        calendar_config=config,
        action_lifetime=ACTION_LIFETIME,
        approval_lifetime=APPROVAL_LIFETIME,
    )
    executor = TaskExecutionCoordinator(
        TaskRepository(sessions),
        approvals,
        TaskReadService(sessions),
        adapter=adapter,
        definition=definition,
        capability_policy=policy,
    )
    return CalendarServices(proposals=proposals, executor=executor, approver=setup.approver)


__all__ = [
    "ACTION_LIFETIME",
    "APPROVAL_LIFETIME",
    "CALENDAR_DERIVATION_VERSION",
    "CalendarProposalsUnavailable",
    "CalendarServices",
    "ClientBuilder",
    "build_calendar_services",
    "calendar_action_definition",
    "calendar_capability_policy",
]
