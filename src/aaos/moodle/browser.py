"""Course-only restricted Moodle source; it cannot create a complete snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeAlias, cast

from aaos.academic.source import RawCourse, SourceCollectionFailure
from aaos.moodle.browser_models import (
    BrowserPolicyError,
    BrowserProfileBusyError,
    acquire_browser_profile_lock,
    ensure_browser_profile_binding,
    resolve_browser_profile_paths,
)
from aaos.moodle.browser_navigation import (
    PlaywrightRestrictedNavigator,
    RestrictedBrowserNavigator,
)
from aaos.moodle.browser_parsing import parse_course_index
from aaos.moodle.collection import CourseDiscovery, CourseDiscoveryResult, EntityBundleResult
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

BrowserSmokeFailureStage: TypeAlias = Literal[
    "PROFILE",
    "CONTEXT_LAUNCH",
    "AUTH_REQUIRED",
    "AUTH_PROOF",
    "AUTH_TIMEOUT",
    "AUTH_PAGE_CLEANUP",
    "TRANSITION_PAGES_REMAIN",
    "INTERCEPTION_INSTALL",
    "COLLECTION_PAGE_CREATE",
    "REQUEST_POLICY",
    "UNEXPECTED_REDIRECT",
    "NAVIGATION",
    "PARSER_SCHEMA",
    "COURSE_IDENTITY",
    "PAGINATION",
    "COMPLETENESS",
    "CLEANUP",
]


class BrowserSmokeFailure(BrowserPolicyError):
    """Carry one fixed, non-sensitive same-context smoke failure stage."""

    def __init__(self, stage: BrowserSmokeFailureStage) -> None:
        super().__init__()
        self.stage = stage


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


def _close_auth_pages(context: object) -> None:
    """Remove every human-auth page before the restricted collection transition."""
    pages = getattr(context, "pages", None)
    if not isinstance(pages, list):
        raise BrowserSmokeFailure("AUTH_PAGE_CLEANUP")
    for page in tuple(pages):
        try:
            getattr(page, "close")()
        except (AttributeError, OSError, TypeError, ValueError):
            raise BrowserSmokeFailure("AUTH_PAGE_CLEANUP") from None
    remaining = getattr(context, "pages", None)
    if not isinstance(remaining, list) or remaining:
        raise BrowserSmokeFailure("TRANSITION_PAGES_REMAIN")


def _collection_failure_stage(result: SourceCollectionFailure) -> BrowserSmokeFailureStage:
    """Map existing typed source failures to fixed CLI-safe orchestration stages."""
    stages: dict[str, BrowserSmokeFailureStage] = {
        "browser_collection_page_create": "COLLECTION_PAGE_CREATE",
        "browser_request_policy": "REQUEST_POLICY",
        "browser_unexpected_redirect": "UNEXPECTED_REDIRECT",
        "browser_navigation": "NAVIGATION",
        "browser_parser_schema": "PARSER_SCHEMA",
    }
    return stages.get(result.code, "NAVIGATION")


def _run_same_context_browser_course_smoke(
    *,
    settings: MoodleSettings,
    paths: AuthPaths,
    launch_persistent_context: Callable[..., object],
) -> CourseDiscoveryResult:
    """Authenticate and collect courses in one owned persistent browser lifetime."""
    if settings.mode != "browser":
        raise BrowserSmokeFailure("PROFILE")
    try:
        profile = resolve_browser_profile_paths(paths, settings.base_origin)
        lease = acquire_browser_profile_lock(profile)
    except (BrowserPolicyError, BrowserProfileBusyError, OSError, TypeError, ValueError):
        raise BrowserSmokeFailure("PROFILE") from None
    with lease:
        try:
            ensure_browser_profile_binding(
                profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
            )
        except (BrowserPolicyError, OSError, TypeError, ValueError):
            raise BrowserSmokeFailure("PROFILE") from None
        try:
            context = launch_persistent_context(
                channel="msedge",
                headless=False,
                user_data_dir=profile.profile_dir,
                accept_downloads=False,
                service_workers="block",
            )
        except (OSError, TimeoutError, TypeError, ValueError):
            raise BrowserSmokeFailure("CONTEXT_LAUNCH") from None

        operation_failed = False
        try:
            try:
                auth_page = getattr(context, "new_page")()
                auth_page.goto(
                    settings.base_origin + "/current/login/index.php",
                    wait_until="domcontentloaded",
                    timeout=600_000,
                )
            except TimeoutError:
                raise BrowserSmokeFailure("AUTH_TIMEOUT") from None
            except (AttributeError, OSError, TypeError, ValueError):
                raise BrowserSmokeFailure("AUTH_REQUIRED") from None
            try:
                authenticated = wait_for_authenticated_course_index(
                    auth_page,
                    course_index_url=settings.base_origin + "/current/my/courses.php",
                    timeout_seconds=600,
                )
            except TimeoutError:
                raise BrowserSmokeFailure("AUTH_TIMEOUT") from None
            except (AttributeError, OSError, TypeError, ValueError):
                raise BrowserSmokeFailure("AUTH_PROOF") from None
            if not authenticated:
                raise BrowserSmokeFailure("AUTH_PROOF")
            _close_auth_pages(context)
            try:
                navigator = PlaywrightRestrictedNavigator(cast(Any, context), settings.base_origin)
            except (BrowserPolicyError, OSError, TypeError, ValueError):
                raise BrowserSmokeFailure("INTERCEPTION_INSTALL") from None
            result = discover_courses_for_smoke(
                MoodleBrowserSource(navigator, "moodle:just:browser-smoke", settings.base_origin)
            )
            if isinstance(result, SourceCollectionFailure):
                raise BrowserSmokeFailure(_collection_failure_stage(result))
            return result
        except BrowserSmokeFailure:
            operation_failed = True
            raise
        finally:
            try:
                getattr(context, "close")()
            except (AttributeError, OSError, TypeError, ValueError):
                if not operation_failed:
                    raise BrowserSmokeFailure("CLEANUP") from None


def run_configured_browser_course_smoke() -> int:
    """Run the separately invoked, memory-only COURSE_INDEX smoke and stop."""
    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        _config, settings, paths = load_runtime_moodle_configuration()
        result = _run_same_context_browser_course_smoke(
            settings=settings,
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
        )
    except BrowserSmokeFailure as failure:
        print("COURSE_DISCOVERY_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    except (BrowserPolicyError, OSError, TimeoutError, ValueError, TypeError):
        print("COURSE_DISCOVERY_FAILED")
        print("FAILURE_STAGE=PROFILE")
        return 1
    if isinstance(result, CourseDiscovery):
        print("COURSE_DISCOVERY_PASS")
        print(f"COURSE_COUNT={len(result.courses)}")
        return 0
    print("COURSE_DISCOVERY_FAILED")
    print("FAILURE_STAGE=NAVIGATION")
    return 1
