"""Phase 12A: everyday Calendar proposals are off unless fully and validly configured."""

from __future__ import annotations

from pathlib import Path

import pytest

from aaos.config import AppSettings, settings_from_environment
from aaos.integrations.google_calendar.setup import (
    CALENDAR_SCOPE,
    PROVIDER_ADAPTER_VERSION,
    resolve_calendar_proposals,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CALENDAR = "0123456789abcdef@group.calendar.google.com"
SERVICE = "aaos-calendar@example-project.iam.gserviceaccount.com"


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "service-account.json"
    path.write_text("{}", encoding="utf-8")  # never opened by the resolver
    return path


def _environment(key_file: Path, **overrides: str | None) -> dict[str, str]:
    values: dict[str, str | None] = {
        "AAOS_CALENDAR_PROPOSALS": "on",
        "AAOS_LOCAL_APPROVER_IDENTITY": "owner",
        "AAOS_LOCAL_APPROVER_CONTEXT_ID": "aaos-laptop-1",
        "AAOS_GOOGLE_CALENDAR_ID": CALENDAR,
        "AAOS_GOOGLE_SERVICE_IDENTITY": SERVICE,
        "AAOS_GOOGLE_CREDENTIAL_PATH": str(key_file),
        "AAOS_CALENDAR_TIMEZONE": "Asia/Amman",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def _resolve(environment: dict[str, str]):  # type: ignore[no-untyped-def]
    return resolve_calendar_proposals(environment, settings_from_environment(environment))


# --- the approver settings -------------------------------------------------------------------


def test_the_approver_settings_are_read_when_both_are_valid() -> None:
    settings = settings_from_environment(
        {"AAOS_LOCAL_APPROVER_IDENTITY": "owner", "AAOS_LOCAL_APPROVER_CONTEXT_ID": "aaos-laptop-1"}
    )
    assert settings.local_approver_identity == "owner"
    assert settings.local_approver_context_id == "aaos-laptop-1"


@pytest.mark.parametrize(
    "environment",
    [
        {"AAOS_LOCAL_APPROVER_IDENTITY": "owner"},
        {"AAOS_LOCAL_APPROVER_CONTEXT_ID": "aaos-laptop-1"},
        {
            "AAOS_LOCAL_APPROVER_IDENTITY": "owner with spaces",
            "AAOS_LOCAL_APPROVER_CONTEXT_ID": "x",
        },
        {"AAOS_LOCAL_APPROVER_IDENTITY": " ", "AAOS_LOCAL_APPROVER_CONTEXT_ID": "x"},
    ],
    ids=["identity-only", "context-only", "unsafe-identity", "blank-identity"],
)
def test_partial_or_invalid_approver_settings_leave_no_approver_and_never_crash(
    environment: dict[str, str],
) -> None:
    settings = settings_from_environment(environment)
    assert settings.local_approver_identity is None
    assert settings.local_approver_context_id is None


def test_without_approver_settings_nothing_changes() -> None:
    assert settings_from_environment({}).local_approver_identity is None


# --- the switch ------------------------------------------------------------------------------


def test_proposals_are_off_by_default_even_with_everything_else_set(key_file: Path) -> None:
    for switch in (None, "", "off", "ON ", "true", "1"):
        setup = _resolve(_environment(key_file, AAOS_CALENDAR_PROPOSALS=switch))
        assert setup.state == "OFF", switch
        assert setup.config is None and setup.approver is None and setup.problems == ()


def test_a_complete_configuration_is_on_with_the_fixed_scope_and_version(key_file: Path) -> None:
    setup = _resolve(_environment(key_file))
    assert setup.state == "ON" and setup.problems == ()
    assert setup.config is not None
    assert setup.config.calendar_id == CALENDAR
    assert setup.config.expected_service_identity == SERVICE
    assert (
        setup.config.oauth_scope
        == CALENDAR_SCOPE
        == "https://www.googleapis.com/auth/calendar.events"
    )
    assert (
        setup.config.provider_adapter_version == PROVIDER_ADAPTER_VERSION == "calendar-adapter-v1"
    )
    assert setup.config.enabled is True
    assert setup.approver is not None
    assert setup.approver.to_dict()["approver_identity"] == "owner"
    assert setup.timezone == "Asia/Amman"


# --- what is refused -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"AAOS_LOCAL_APPROVER_IDENTITY": None}, "AAOS_LOCAL_APPROVER_IDENTITY"),
        ({"AAOS_LOCAL_APPROVER_CONTEXT_ID": "bad context"}, "AAOS_LOCAL_APPROVER_CONTEXT_ID"),
        ({"AAOS_GOOGLE_CALENDAR_ID": None}, "AAOS_GOOGLE_CALENDAR_ID"),
        ({"AAOS_GOOGLE_CALENDAR_ID": "primary"}, "AAOS_GOOGLE_CALENDAR_ID"),
        ({"AAOS_GOOGLE_CALENDAR_ID": "someone@gmail.com"}, "AAOS_GOOGLE_CALENDAR_ID"),
        ({"AAOS_GOOGLE_SERVICE_IDENTITY": None}, "AAOS_GOOGLE_SERVICE_IDENTITY"),
        ({"AAOS_GOOGLE_SERVICE_IDENTITY": "person@gmail.com"}, "AAOS_GOOGLE_SERVICE_IDENTITY"),
        ({"AAOS_GOOGLE_CREDENTIAL_PATH": None}, "AAOS_GOOGLE_CREDENTIAL_PATH"),
        ({"AAOS_GOOGLE_CREDENTIAL_PATH": "relative/key.json"}, "AAOS_GOOGLE_CREDENTIAL_PATH"),
        (
            {
                "AAOS_GOOGLE_CREDENTIAL_PATH": str(
                    REPOSITORY_ROOT / "data" / "credentials" / "k.json"
                )
            },
            "AAOS_GOOGLE_CREDENTIAL_PATH",
        ),
        ({"AAOS_CALENDAR_TIMEZONE": None}, "AAOS_CALENDAR_TIMEZONE"),
    ],
    ids=[
        "no-approver-identity",
        "unsafe-approver-context",
        "no-calendar",
        "primary-calendar",
        "personal-calendar",
        "no-service-identity",
        "not-a-service-account",
        "no-credential-path",
        "relative-credential-path",
        "credential-in-repository",
        "no-timezone",
    ],
)
def test_each_missing_or_invalid_value_is_misconfigured_and_named_without_its_value(
    key_file: Path, overrides: dict[str, str | None], problem: str
) -> None:
    setup = _resolve(_environment(key_file, **overrides))
    assert setup.state == "MISCONFIGURED"
    assert problem in setup.problems
    assert setup.config is None
    shown = " ".join([repr(setup), str(setup), *setup.problems])
    for value in (CALENDAR, SERVICE, str(key_file), "someone@gmail.com", "person@gmail.com"):
        assert value not in shown


def test_a_missing_key_file_is_misconfigured(tmp_path: Path, key_file: Path) -> None:
    setup = _resolve(
        _environment(key_file, AAOS_GOOGLE_CREDENTIAL_PATH=str(tmp_path / "absent.json"))
    )
    assert setup.state == "MISCONFIGURED"
    assert setup.problems == ("AAOS_GOOGLE_CREDENTIAL_PATH",)


def test_an_on_setup_hides_its_values_from_repr(key_file: Path) -> None:
    setup = _resolve(_environment(key_file))
    shown = repr(setup) + str(setup)
    for value in (CALENDAR, SERVICE, str(key_file), "owner", "aaos-laptop-1"):
        assert value not in shown


def test_the_settings_object_never_holds_calendar_or_credential_values(key_file: Path) -> None:
    settings = settings_from_environment(_environment(key_file))
    assert isinstance(settings, AppSettings)
    flattened = repr(settings)
    for value in (CALENDAR, SERVICE, str(key_file)):
        assert value not in flattened
