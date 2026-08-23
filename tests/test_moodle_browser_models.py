from __future__ import annotations

from pathlib import Path

import pytest

from aaos.moodle.models import AuthPaths


def test_profile_paths_are_confined_below_auth_root(tmp_path: Path) -> None:
    from aaos.moodle.browser_models import resolve_browser_profile_paths

    paths = AuthPaths(tmp_path / "credentials" / "token", tmp_path / "auth" / "moodle" / "just")

    result = resolve_browser_profile_paths(paths, "https://moodle.example.edu")

    assert result.profile_dir == paths.browser_state_dir / "playwright-edge-profile"
    assert result.lock_path.parent == result.profile_dir.parent
    assert result.binding_path.parent == result.profile_dir.parent


def test_profile_lock_is_exclusive_and_does_not_remove_foreign_lock(tmp_path: Path) -> None:
    from aaos.moodle.browser_models import (
        BrowserProfileBusyError,
        acquire_browser_profile_lock,
        resolve_browser_profile_paths,
    )

    paths = AuthPaths(tmp_path / "credentials" / "token", tmp_path / "auth" / "moodle" / "just")
    profile = resolve_browser_profile_paths(paths, "https://moodle.example.edu")
    with acquire_browser_profile_lock(profile):
        with pytest.raises(BrowserProfileBusyError):
            acquire_browser_profile_lock(profile)
    assert not profile.lock_path.exists()


def test_request_policy_blocks_post_and_unlisted_ajax() -> None:
    from aaos.moodle.browser_policy import classify_browser_request

    assert (
        classify_browser_request(
            "POST",
            "https://moodle.example.edu/current/lib/ajax/service.php",
            base_origin="https://moodle.example.edu",
        ).code
        == "BLOCK"
    )
    assert (
        classify_browser_request(
            "GET",
            "https://moodle.example.edu/current/lib/ajax/service.php",
            base_origin="https://moodle.example.edu",
        ).code
        == "BLOCK"
    )


def test_route_builder_accepts_only_approved_numeric_identifiers() -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_policy import build_browser_url

    assert build_browser_url("COURSE_INDEX", base_origin="https://moodle.example.edu") == (
        "https://moodle.example.edu/current/my/courses.php"
    )
    assert build_browser_url(
        "COURSE_DETAIL", base_origin="https://moodle.example.edu", identifier=42
    ).endswith("/current/course/view.php?id=42")
    with pytest.raises(BrowserPolicyError):
        build_browser_url("COURSE_DETAIL", base_origin="https://moodle.example.edu", identifier=0)


def test_pagination_proof_requires_exhaustion_and_contiguous_pages() -> None:
    from aaos.moodle.browser_models import (
        BrowserPolicyError,
        PaginationProof,
        validate_pagination_proof,
    )

    with pytest.raises(BrowserPolicyError):
        validate_pagination_proof(PaginationProof("COURSE_INDEX", (0, 2), True, None, 2))
    with pytest.raises(BrowserPolicyError):
        validate_pagination_proof(PaginationProof("COURSE_INDEX", (0,), False, None, 1))
