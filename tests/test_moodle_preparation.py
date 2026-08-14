from __future__ import annotations

from aaos.moodle.models import MoodleSettings
from aaos.moodle.preparation import CapabilityPreparationResult, prepare_capability

API_SETTINGS = MoodleSettings(
    source_system="moodle:university-primary:student-all-courses",
    base_origin="https://moodle.example.edu",
    mode="api",
    auth_alias="university-primary",
)


def complete_api_observation() -> dict[str, object]:
    return {
        "api_capability_available": True,
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


def test_complete_synthetic_capability_is_ready() -> None:
    result = prepare_capability(API_SETTINGS, complete_api_observation())

    assert isinstance(result, CapabilityPreparationResult)
    assert result.status == "ready"
    assert result.reason_code == "ready"
    assert result.capability is not None
    assert result.capability.source_system == API_SETTINGS.source_system


def test_missing_api_capability_blocks_without_browser_fallback() -> None:
    observed = complete_api_observation()
    observed["api_capability_available"] = False

    result = prepare_capability(API_SETTINGS, observed)

    assert result.status == "blocked"
    assert result.reason_code == "api_capability_unavailable"
    assert result.capability is None


def test_one_course_observation_cannot_claim_all_courses_scope() -> None:
    observed = complete_api_observation()
    observed["complete_scope"] = "one-course-smoke"

    result = prepare_capability(API_SETTINGS, observed)

    assert result.status == "blocked"
    assert result.reason_code == "scope_not_authoritative"
    assert result.capability is None


def test_incomplete_entity_scope_is_blocked() -> None:
    observed = complete_api_observation()
    observed["entity_scope"] = ("course", "assignment")

    result = prepare_capability(API_SETTINGS, observed)

    assert result.status == "blocked"
    assert result.reason_code == "entity_scope_incomplete"
    assert result.capability is None


def test_secret_named_observation_fields_are_rejected() -> None:
    observed = complete_api_observation()
    observed["api_token"] = "synthetic-secret"

    result = prepare_capability(API_SETTINGS, observed)

    assert result.status == "blocked"
    assert result.reason_code == "secret_field_present"
    assert result.capability is None


def test_preparation_does_not_change_selected_mode() -> None:
    browser_settings = MoodleSettings(
        source_system="moodle:university-primary:student-all-courses",
        base_origin="https://moodle.example.edu",
        mode="browser",
        auth_alias="university-primary",
    )
    observed = {
        "browser_capability_available": False,
        "complete_scope": "all-accessible-courses",
    }

    result = prepare_capability(browser_settings, observed)

    assert result.status == "blocked"
    assert result.reason_code == "browser_capability_unavailable"
