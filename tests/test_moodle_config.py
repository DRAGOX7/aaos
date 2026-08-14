from __future__ import annotations

import pytest

from aaos.moodle.models import MoodleSettings


def test_moodle_settings_accepts_https_origin_and_mode() -> None:
    settings = MoodleSettings(
        source_system="moodle:university-primary:student-all-courses",
        base_origin="https://moodle.example.edu",
        mode="api",
        auth_alias="university-primary",
    )

    assert settings.base_origin == "https://moodle.example.edu"
    assert settings.mode == "api"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_system", ""),
        ("source_system", "moodle:bad alias"),
        ("base_origin", "http://moodle.example.edu"),
        ("base_origin", "https://moodle.example.edu/path"),
        ("base_origin", "https://user:password@moodle.example.edu"),
        ("auth_alias", "../other-source"),
        ("auth_alias", "university/primary"),
    ],
)
def test_moodle_settings_rejects_unsafe_values(field: str, value: str) -> None:
    kwargs: dict[str, object] = {
        "source_system": "moodle:university-primary:student-all-courses",
        "base_origin": "https://moodle.example.edu",
        "mode": "api",
        "auth_alias": "university-primary",
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        MoodleSettings(**kwargs)  # type: ignore[arg-type]
