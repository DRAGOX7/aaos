"""Whether everyday Calendar proposals are on (Phase 12, slice 12A).

Pure configuration: no network, and the key file is checked for existence only, never
opened (the client factory reads it later). Guard rails:

* off unless ``AAOS_CALENDAR_PROPOSALS`` is exactly ``on``;
* on only when every value is present and valid: the local approver (from the
  settings), a dedicated group calendar (never ``primary`` or a personal address), a
  service-account identity, an absolute key path outside the repository, and a timezone;
* otherwise ``MISCONFIGURED``, naming only the settings that are wrong, never a value;
* the OAuth scope and the adapter version are fixed here, not configurable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aaos.approval.contracts import TrustedApproverContext
from aaos.config import (
    CALENDAR_TIMEZONE_ENVIRONMENT_KEY,
    LOCAL_APPROVER_CONTEXT_ENVIRONMENT_KEY,
    LOCAL_APPROVER_IDENTITY_ENVIRONMENT_KEY,
    AppSettings,
)

from .contracts import GoogleCalendarConfig

CALENDAR_PROPOSALS_ENVIRONMENT_KEY = "AAOS_CALENDAR_PROPOSALS"
GOOGLE_CALENDAR_ID_ENVIRONMENT_KEY = "AAOS_GOOGLE_CALENDAR_ID"
GOOGLE_SERVICE_IDENTITY_ENVIRONMENT_KEY = "AAOS_GOOGLE_SERVICE_IDENTITY"
GOOGLE_CREDENTIAL_PATH_ENVIRONMENT_KEY = "AAOS_GOOGLE_CREDENTIAL_PATH"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
PROVIDER_ADAPTER_VERSION = "calendar-adapter-v1"

CalendarProposalsState = Literal["OFF", "ON", "MISCONFIGURED"]

# A dedicated calendar has a group-calendar ID; "primary" and personal addresses never
# match, so the owner's main calendar can't be configured by mistake.
_GROUP_CALENDAR_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}@group\.calendar\.google\.com$")
_SERVICE_IDENTITY = re.compile(r"^[a-z][a-z0-9-]{4,62}@[a-z0-9-]{4,62}\.iam\.gserviceaccount\.com$")


@dataclass(frozen=True, slots=True)
class CalendarProposalsSetup:
    """The resolved state; the values themselves never appear in its repr."""

    state: CalendarProposalsState
    problems: tuple[str, ...] = ()
    config: GoogleCalendarConfig | None = field(default=None, repr=False)
    approver: TrustedApproverContext | None = field(default=None, repr=False)
    timezone: str | None = field(default=None, repr=False)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _credential_path_ok(raw: str) -> bool:
    try:
        path = Path(raw)
        if not path.is_absolute():
            return False
        resolved = path.resolve()
        return not resolved.is_relative_to(_repository_root()) and resolved.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def resolve_calendar_proposals(
    environment: Mapping[str, str], settings: AppSettings
) -> CalendarProposalsSetup:
    """Decide ON, OFF or MISCONFIGURED from the environment and the settings."""
    if environment.get(CALENDAR_PROPOSALS_ENVIRONMENT_KEY) != "on":
        return CalendarProposalsSetup(state="OFF")

    problems: list[str] = []
    approver: TrustedApproverContext | None = None
    if settings.local_approver_identity is None or settings.local_approver_context_id is None:
        problems += [
            LOCAL_APPROVER_IDENTITY_ENVIRONMENT_KEY,
            LOCAL_APPROVER_CONTEXT_ENVIRONMENT_KEY,
        ]
    else:
        approver = TrustedApproverContext(
            settings.local_approver_identity, settings.local_approver_context_id
        )

    calendar_id = environment.get(GOOGLE_CALENDAR_ID_ENVIRONMENT_KEY, "").strip()
    if not _GROUP_CALENDAR_ID.fullmatch(calendar_id):
        problems.append(GOOGLE_CALENDAR_ID_ENVIRONMENT_KEY)
    service_identity = environment.get(GOOGLE_SERVICE_IDENTITY_ENVIRONMENT_KEY, "").strip()
    if not _SERVICE_IDENTITY.fullmatch(service_identity):
        problems.append(GOOGLE_SERVICE_IDENTITY_ENVIRONMENT_KEY)
    credential_path = environment.get(GOOGLE_CREDENTIAL_PATH_ENVIRONMENT_KEY, "").strip()
    if not _credential_path_ok(credential_path):
        problems.append(GOOGLE_CREDENTIAL_PATH_ENVIRONMENT_KEY)
    if settings.calendar_timezone is None:
        problems.append(CALENDAR_TIMEZONE_ENVIRONMENT_KEY)

    if problems:
        return CalendarProposalsSetup(state="MISCONFIGURED", problems=tuple(problems))
    return CalendarProposalsSetup(
        state="ON",
        config=GoogleCalendarConfig(
            calendar_id=calendar_id,
            expected_service_identity=service_identity,
            oauth_scope=CALENDAR_SCOPE,
            credential_path=credential_path,
            provider_adapter_version=PROVIDER_ADAPTER_VERSION,
            enabled=True,
        ),
        approver=approver,
        timezone=settings.calendar_timezone,
    )


__all__ = [
    "CALENDAR_PROPOSALS_ENVIRONMENT_KEY",
    "CALENDAR_SCOPE",
    "GOOGLE_CALENDAR_ID_ENVIRONMENT_KEY",
    "GOOGLE_CREDENTIAL_PATH_ENVIRONMENT_KEY",
    "GOOGLE_SERVICE_IDENTITY_ENVIRONMENT_KEY",
    "PROVIDER_ADAPTER_VERSION",
    "CalendarProposalsSetup",
    "CalendarProposalsState",
    "resolve_calendar_proposals",
]
