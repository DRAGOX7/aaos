from __future__ import annotations

from scripts.phase9.qualify_clean_setup import validate_configuration


def test_configuration_classification_is_safe_and_non_networked() -> None:
    report = validate_configuration(
        {
            "AAOS_ENV": "test",
            "AAOS_DATABASE_PATH": "data/database/aaos.db",
            "LM_STUDIO_BASE_URL": None,
            "GOOGLE_CLIENT_SECRET": "synthetic-only",
        }
    )
    assert report.statuses["AAOS_ENV"] == "VALID"
    assert report.statuses["AAOS_DATABASE_PATH"] == "VALID"
    assert report.statuses["LM_STUDIO_BASE_URL"] == "MISSING"
    assert report.statuses["GOOGLE_CLIENT_SECRET"] == "VALID"
    assert report.external_calls == 0
    assert "synthetic-only" not in report.to_dict()


def test_configuration_rejects_malformed_environment_without_reading_services() -> None:
    report = validate_configuration({"AAOS_ENV": "unknown", "AAOS_DATABASE_PATH": ""})
    assert report.statuses == {
        "AAOS_ENV": "MISCONFIGURED",
        "AAOS_DATABASE_PATH": "MISSING",
    }
    assert report.external_calls == 0
