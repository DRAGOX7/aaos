from __future__ import annotations

import dataclasses
from typing import get_args

import pytest

from aaos.moodle.models import (
    ApiOperation,
    BrowserRoute,
    EntityType,
    IdentityStrategy,
    MoodleCapability,
    MoodleSettings,
    PaginationStrategy,
    RedirectPolicy,
    TimestampStrategy,
    validate_capability,
)

SETTINGS = MoodleSettings(
    source_system="moodle:university-primary:student-all-courses",
    base_origin="https://moodle.example.edu",
    mode="api",
    auth_alias="university-primary",
)


def complete_capability(**overrides: object) -> MoodleCapability:
    values: dict[str, object] = {
        "source_system": SETTINGS.source_system,
        "base_origin": SETTINGS.base_origin,
        "mode": "api",
        "complete_scope": "all-accessible-courses",
        "entity_scope": (
            "course",
            "assignment",
            "announcement",
            "remote_file_metadata",
        ),
        "approved_api_operations": (
            "courses",
            "assignments",
            "announcements",
            "remote_files",
        ),
        "approved_browser_routes": (),
        "pagination_strategy": "offset_total",
        "identity_strategy": "provider_id",
        "timestamp_strategy": "unix_utc",
        "redirect_policy": "disabled",
        "redirect_origins": (),
        "required_version": None,
        "evidence_notes": "synthetic capability evidence",
    }
    values.update(overrides)
    return MoodleCapability(**values)  # type: ignore[arg-type]


def test_capability_validates_against_settings() -> None:
    validate_capability(SETTINGS, complete_capability())


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_system", "moodle:other:student"),
        ("base_origin", "https://other.example.edu"),
        ("mode", "browser"),
        ("complete_scope", ""),
        ("entity_scope", ("course",)),
        ("approved_api_operations", ()),
        ("pagination_strategy", "arbitrary_prose"),
        ("identity_strategy", "title"),
        ("timestamp_strategy", "local_machine"),
        ("redirect_policy", "follow_anywhere"),
    ],
)
def test_capability_rejects_mismatch_or_unsupported_policy(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        validate_capability(SETTINGS, complete_capability(**{field: value}))


def test_capability_is_frozen_and_contains_only_non_secret_fields() -> None:
    capability = complete_capability()

    with pytest.raises(dataclasses.FrozenInstanceError):
        capability.complete_scope = "other"  # type: ignore[misc]

    field_names = {field.name for field in dataclasses.fields(capability)}
    assert field_names.isdisjoint({"password", "token", "cookie", "session_state"})


def test_typed_policy_vocabulary_is_closed() -> None:
    assert get_args(ApiOperation) == (
        "courses",
        "assignments",
        "announcements",
        "remote_files",
    )
    assert get_args(BrowserRoute)
    assert get_args(EntityType)
    assert get_args(IdentityStrategy)
    assert get_args(PaginationStrategy)
    assert get_args(RedirectPolicy)
    assert get_args(TimestampStrategy)
