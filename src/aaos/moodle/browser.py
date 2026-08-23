"""Course-only restricted Moodle source; it cannot create a complete snapshot."""

from __future__ import annotations

from typing import Any, cast

from aaos.academic.source import RawCourse, SourceCollectionFailure
from aaos.moodle.browser_models import BrowserPolicyError
from aaos.moodle.browser_navigation import RestrictedBrowserNavigator
from aaos.moodle.browser_parsing import parse_course_index
from aaos.moodle.collection import CourseDiscovery, CourseDiscoveryResult, EntityBundleResult


class MoodleBrowserSource:
    """Read exactly My Courses into memory; entity collection remains unapproved."""

    def __init__(
        self, navigator: RestrictedBrowserNavigator, source_system: str, base_origin: str
    ) -> None:
        self._navigator = navigator
        self._source_system = source_system
        self._base_origin = base_origin

    def discover_courses(self) -> CourseDiscoveryResult:
        try:
            document = self._navigator.open("COURSE_INDEX")
        except BrowserPolicyError:
            stage = getattr(self._navigator, "failure_stage", "NAVIGATION")
            return SourceCollectionFailure(self._source_system, f"browser_{stage.lower()}")
        try:
            parsed = parse_course_index(document.html, expected_origin=self._base_origin)
            return CourseDiscovery(parsed.records, is_complete=True)
        except BrowserPolicyError:
            return SourceCollectionFailure(self._source_system, "browser_parser_schema")

    def collect_course_entities(self, course: RawCourse) -> EntityBundleResult:
        del course
        return SourceCollectionFailure(self._source_system, "browser_entity_scope_not_approved")


def discover_courses_for_smoke(source: MoodleBrowserSource) -> CourseDiscoveryResult:
    """Expose the sole first-smoke operation without normalization or persistence."""
    return source.discover_courses()


def run_configured_browser_course_smoke() -> int:
    """Run the separately invoked, memory-only COURSE_INDEX smoke and stop."""
    from aaos.moodle.browser_models import (
        acquire_browser_profile_lock,
        resolve_browser_profile_paths,
        validate_browser_profile_binding,
    )
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator
    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    navigator: PlaywrightRestrictedNavigator | None = None
    try:
        _config, settings, paths = load_runtime_moodle_configuration()
        profile = resolve_browser_profile_paths(paths, settings.base_origin)
        with acquire_browser_profile_lock(profile):
            validate_browser_profile_binding(
                profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
            )
            context = launch_msedge_persistent_context(
                channel="msedge",
                headless=False,
                user_data_dir=profile.profile_dir,
                accept_downloads=False,
                service_workers="block",
            )
            navigator = PlaywrightRestrictedNavigator(cast(Any, context), settings.base_origin)
            result = discover_courses_for_smoke(
                MoodleBrowserSource(navigator, "moodle:just:browser-smoke", settings.base_origin)
            )
    except (BrowserPolicyError, OSError, ValueError, TypeError):
        print("COURSE_DISCOVERY_FAILED")
        return 1
    finally:
        if navigator is not None:
            try:
                navigator.close()
            except BrowserPolicyError:
                print("COURSE_DISCOVERY_FAILED")
                return 1
    if isinstance(result, CourseDiscovery):
        print(f"COURSE_DISCOVERY_COMPLETE count={len(result.courses)}")
        return 0
    print("COURSE_DISCOVERY_FAILED")
    print(f"FAILURE_STAGE={result.code.removeprefix('browser_').upper()}")
    if result.code == "browser_unexpected_redirect" and navigator.redirect_class is not None:
        print(f"REDIRECT_CLASS={navigator.redirect_class}")
    return 1
