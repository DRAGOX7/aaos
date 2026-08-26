"""Restricted Moodle browser source for proven read-only academic metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, cast

from playwright.sync_api import Error as PlaywrightError

from aaos.academic.source import RawAnnouncement, RawCourse, SourceCollectionFailure
from aaos.moodle.browser_models import (
    BrowserPolicyError,
    BrowserProfileBusyError,
    acquire_browser_profile_lock,
    ensure_browser_profile_binding,
    resolve_browser_profile_paths,
)
from aaos.moodle.browser_navigation import (
    BrowserDocument,
    BrowserRequestSummary,
    MoodleHandlerClass,
    PlaywrightRestrictedNavigator,
    RequestResourceType,
    RestrictedBrowserNavigator,
)
from aaos.moodle.browser_parsing import (
    CourseIndexLoadingDiagnostic,
    CourseIndexStructureDiagnostic,
    diagnose_course_index_loading,
    diagnose_course_index_structure,
    parse_frontend_announcement_list,
    parse_frontend_assignment_list,
    parse_frontend_course_entities,
    parse_frontend_course_index,
)
from aaos.moodle.collection import (
    CourseDiscovery,
    CourseDiscoveryResult,
    EntityBundle,
    EntityBundleResult,
)
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


@dataclass(frozen=True, slots=True)
class CourseIndexLoadingResult:
    """Non-content loading evidence from one restricted COURSE_INDEX observation."""

    diagnostic: CourseIndexLoadingDiagnostic
    requests: BrowserRequestSummary

    def render(self) -> str:
        lines = [
            self.diagnostic.render(),
            f"SAME_ORIGIN_XHR_FETCH_COUNT={self.requests.same_origin_xhr_fetch_count}",
            f"MOODLE_AJAX_SERVICE_REQUEST_COUNT={self.requests.moodle_ajax_service_count}",
            f"BLOCKED_POST_MOODLE_AJAX_SERVICE_COUNT={self.requests.blocked_post_moodle_ajax_service_count}",
            f"ALLOWED_STATIC_REQUEST_COUNT={self.requests.allowed_static_request_count}",
        ]
        if self.requests.blocked_post_moodle_ajax_service_count:
            lines.extend(
                (
                    "COURSE_DYNAMIC_DATA=POLICY_CONTRADICTION",
                    "REQUEST_METHOD=POST",
                    "REQUEST_CLASS=MOODLE_AJAX_SERVICE",
                )
            )
        elif self.diagnostic.initial != self.diagnostic.settled:
            lines.append("COURSE_DYNAMIC_DATA=ASYNC_RENDER_CONFIRMED")
        else:
            lines.append("COURSE_DYNAMIC_DATA=UNKNOWN_RENDERING")
        return "\n".join(lines)


BrowserCourseIndexResult: TypeAlias = (
    CourseDiscoveryResult | CourseIndexStructureDiagnostic | CourseIndexLoadingResult
)


class BrowserSmokeFailure(BrowserPolicyError):
    """Carry one fixed, non-sensitive same-context smoke failure stage."""

    def __init__(self, stage: BrowserSmokeFailureStage) -> None:
        super().__init__()
        self.stage = stage


class _EntityBrowserNavigator(Protocol):
    failure_stage: object

    def open_course_detail(self, identifier: int) -> BrowserDocument: ...

    def open_announcement_list(self, identifier: int) -> BrowserDocument: ...


class MoodleBrowserSource:
    """Read proven Moodle course and entity metadata into memory."""

    def __init__(
        self, navigator: RestrictedBrowserNavigator, source_system: str, base_origin: str
    ) -> None:
        self._navigator = navigator
        self._source_system = source_system
        self._base_origin = base_origin

    def discover_courses(self) -> CourseDiscoveryResult:
        try:
            document = self._navigator.open_frontend_course_index()
        except BrowserPolicyError:
            stage = getattr(self._navigator, "failure_stage", "NAVIGATION")
            return SourceCollectionFailure(self._source_system, f"browser_{stage.lower()}")
        try:
            if document.course_index_complete:
                courses = _courses_from_complete_document(document)
            else:
                courses = parse_frontend_course_index(
                    document.html, expected_origin=self._base_origin
                )
            return CourseDiscovery(courses, is_complete=True)
        except BrowserPolicyError:
            return SourceCollectionFailure(self._source_system, "browser_parser_schema")

    def collect_course_entities(self, course: RawCourse) -> EntityBundleResult:
        identifier = _bounded_course_identifier(course.source_id)
        if identifier is None:
            return SourceCollectionFailure(self._source_system, "browser_course_identity")
        navigator = cast(_EntityBrowserNavigator, self._navigator)
        try:
            course_document = navigator.open_course_detail(identifier)
            course_entities = parse_frontend_course_entities(
                course_document.html,
                course_source_id=course.source_id,
                expected_origin=self._base_origin,
            )
            assignments = parse_frontend_assignment_list(
                course_document.html,
                course_source_id=course.source_id,
                expected_origin=self._base_origin,
            )
            announcements: list[RawAnnouncement] = []
            for forum_id in course_entities.announcement_forum_ids:
                forum_document = navigator.open_announcement_list(int(forum_id))
                announcements.extend(
                    parse_frontend_announcement_list(
                        forum_document.html,
                        course_source_id=course.source_id,
                        expected_origin=self._base_origin,
                    )
                )
            return EntityBundle(
                course_source_id=course.source_id,
                assignments=assignments,
                announcements=tuple(announcements),
                remote_files=course_entities.remote_files,
                is_complete=True,
            )
        except (AttributeError, BrowserPolicyError, OSError, TypeError, ValueError):
            stage = getattr(self._navigator, "failure_stage", "NAVIGATION")
            if isinstance(stage, str) and stage != "NONE":
                return SourceCollectionFailure(self._source_system, f"browser_{stage.lower()}")
            return SourceCollectionFailure(self._source_system, "browser_parser_schema")


def _courses_from_complete_document(document: BrowserDocument) -> tuple[RawCourse, ...]:
    """Require the complete response's redundant identity views to agree."""
    if not isinstance(document.course_ids, tuple) or not isinstance(document.course_records, tuple):
        raise BrowserPolicyError()

    record_ids: list[str] = []
    seen: set[str] = set()
    courses: list[RawCourse] = []
    for record in document.course_records:
        if (
            not isinstance(record, tuple)
            or len(record) != 2
            or not isinstance(record[0], str)
            or not isinstance(record[1], str)
            or record[0] in seen
        ):
            raise BrowserPolicyError()
        identifier, title = record
        seen.add(identifier)
        record_ids.append(identifier)
        courses.append(RawCourse(source_id=identifier, title=title))

    if document.course_ids != tuple(record_ids):
        raise BrowserPolicyError()
    return tuple(courses)


def _bounded_course_identifier(value: object) -> int | None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 10
        or not value.isascii()
        or not value.isdecimal()
        or value == "0"
    ):
        return None
    return int(value)


def discover_courses_for_smoke(source: MoodleBrowserSource) -> CourseDiscoveryResult:
    """Expose the sole first-smoke operation without normalization or persistence."""
    return source.discover_courses()


def _same_pages(actual: object, expected: tuple[object, ...]) -> bool:
    """Compare browser page collections by identity without exposing their contents."""
    return (
        isinstance(actual, list)
        and len(actual) == len(expected)
        and all(sum(page is candidate for page in actual) == 1 for candidate in expected)
    )


def _create_transition_sentinel(context: object) -> tuple[tuple[object, ...], object]:
    """Create one inert AAOS-owned page before closing the final auth page."""
    pages = getattr(context, "pages", None)
    if not isinstance(pages, list) or not pages:
        raise BrowserSmokeFailure("AUTH_PAGE_CLEANUP")
    auth_pages = tuple(pages)
    try:
        sentinel = getattr(context, "new_page")()
    except (AttributeError, OSError, PlaywrightError, TypeError, ValueError):
        raise BrowserSmokeFailure("AUTH_PAGE_CLEANUP") from None
    if (
        any(sentinel is page for page in auth_pages)
        or getattr(sentinel, "url", None) != "about:blank"
        or not _same_pages(getattr(context, "pages", None), (*auth_pages, sentinel))
    ):
        raise BrowserSmokeFailure("TRANSITION_PAGES_REMAIN")
    return auth_pages, sentinel


def _close_auth_pages(context: object, auth_pages: tuple[object, ...], sentinel: object) -> None:
    """Remove every human-auth page while retaining only the inert sentinel."""
    for page in auth_pages:
        try:
            getattr(page, "close")()
        except (AttributeError, OSError, PlaywrightError, TypeError, ValueError):
            raise BrowserSmokeFailure("AUTH_PAGE_CLEANUP") from None
    if (
        not _same_pages(getattr(context, "pages", None), (sentinel,))
        or getattr(sentinel, "url", None) != "about:blank"
    ):
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


def _navigator_failure_stage(navigator: object) -> BrowserSmokeFailureStage:
    """Map the navigator's already-sanitized status to CLI-safe orchestration output."""
    stages: dict[str, BrowserSmokeFailureStage] = {
        "COLLECTION_PAGE_CREATE": "COLLECTION_PAGE_CREATE",
        "REQUEST_POLICY": "REQUEST_POLICY",
        "UNEXPECTED_REDIRECT": "UNEXPECTED_REDIRECT",
        "NAVIGATION": "NAVIGATION",
    }
    return stages.get(getattr(navigator, "failure_stage", "NONE"), "NAVIGATION")


def _close_context_after_operation(context: object) -> None:
    """Close the owned context, accepting only an already-closed Playwright target."""
    try:
        getattr(context, "close")()
    except PlaywrightError as error:
        if type(error).__name__ != "TargetClosedError":
            raise BrowserSmokeFailure("CLEANUP") from None
        try:
            pages = getattr(context, "pages")
        except PlaywrightError:
            return
        if isinstance(pages, list) and not pages:
            return
        raise BrowserSmokeFailure("CLEANUP") from None
    except (AttributeError, OSError, TypeError, ValueError):
        raise BrowserSmokeFailure("CLEANUP") from None


def _run_same_context_browser_course_smoke(
    *,
    settings: MoodleSettings,
    paths: AuthPaths,
    launch_persistent_context: Callable[..., object],
    canonical_wwwroot: str | None = None,
    course_index_diagnostic: bool = False,
    course_index_loading_diagnostic: bool = False,
) -> BrowserCourseIndexResult:
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
            auth_pages, sentinel = _create_transition_sentinel(context)
            _close_auth_pages(context, auth_pages, sentinel)
            if course_index_diagnostic:
                try:
                    navigator = PlaywrightRestrictedNavigator(
                        cast(Any, context),
                        settings.base_origin,
                        canonical_wwwroot=canonical_wwwroot,
                        transition_sentinel=cast(Any, sentinel),
                    )
                except (BrowserPolicyError, OSError, TypeError, ValueError):
                    raise BrowserSmokeFailure("INTERCEPTION_INSTALL") from None
                try:
                    document = navigator.open_course_index_document()
                    return diagnose_course_index_structure(
                        document.html, expected_origin=settings.base_origin
                    )
                except BrowserPolicyError:
                    raise BrowserSmokeFailure(_navigator_failure_stage(navigator)) from None
            if course_index_loading_diagnostic:
                try:
                    navigator = PlaywrightRestrictedNavigator(
                        cast(Any, context),
                        settings.base_origin,
                        canonical_wwwroot=canonical_wwwroot,
                        transition_sentinel=cast(Any, sentinel),
                    )
                except (BrowserPolicyError, OSError, TypeError, ValueError):
                    raise BrowserSmokeFailure("INTERCEPTION_INSTALL") from None
                try:
                    samples = navigator.observe_course_index_loading()
                    return CourseIndexLoadingResult(
                        diagnostic=diagnose_course_index_loading(
                            samples, expected_origin=settings.base_origin
                        ),
                        requests=navigator.request_summary,
                    )
                except BrowserPolicyError:
                    raise BrowserSmokeFailure(_navigator_failure_stage(navigator)) from None
            try:
                navigator = PlaywrightRestrictedNavigator(
                    cast(Any, context),
                    settings.base_origin,
                    canonical_wwwroot=canonical_wwwroot,
                    transition_sentinel=cast(Any, sentinel),
                )
            except (BrowserPolicyError, OSError, TypeError, ValueError):
                raise BrowserSmokeFailure("INTERCEPTION_INSTALL") from None
            result = discover_courses_for_smoke(
                MoodleBrowserSource(navigator, settings.source_system, settings.base_origin)
            )
            if isinstance(result, SourceCollectionFailure):
                raise BrowserSmokeFailure(_collection_failure_stage(result))
            return result
        except BrowserSmokeFailure:
            operation_failed = True
            raise
        finally:
            try:
                _close_context_after_operation(context)
            except BrowserSmokeFailure:
                if not operation_failed:
                    raise


def run_configured_browser_course_smoke() -> int:
    """Run the separately invoked, memory-only COURSE_INDEX smoke and stop."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        settings = replace(
            runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
        )
        result = _run_same_context_browser_course_smoke(
            settings=settings,
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
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


def run_configured_course_index_structure_diagnostic() -> int:
    """Run one explicit, content-free COURSE_INDEX structure diagnostic and stop."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        settings = replace(
            runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
        )
        result = _run_same_context_browser_course_smoke(
            settings=settings,
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("COURSE_INDEX_STRUCTURE_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    except (BrowserPolicyError, OSError, TimeoutError, ValueError, TypeError):
        print("COURSE_INDEX_STRUCTURE_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=PROFILE")
        return 1
    if isinstance(result, CourseIndexStructureDiagnostic):
        print(result.render())
        return 0
    print("COURSE_INDEX_STRUCTURE_DIAGNOSTIC")
    print("DIAGNOSTIC_FAILED")
    print("FAILURE_STAGE=NAVIGATION")
    return 1


def run_configured_course_index_loading_diagnostic() -> int:
    """Run one explicit bounded loading observation and stop before parsing courses."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        settings = replace(
            runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
        )
        result = _run_same_context_browser_course_smoke(
            settings=settings,
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_loading_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("COURSE_INDEX_LOADING_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    except (BrowserPolicyError, OSError, TimeoutError, ValueError, TypeError):
        print("COURSE_INDEX_LOADING_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=PROFILE")
        return 1
    if isinstance(result, CourseIndexLoadingResult):
        print(result.render())
        return 0
    print("COURSE_INDEX_LOADING_DIAGNOSTIC")
    print("DIAGNOSTIC_FAILED")
    print("FAILURE_STAGE=NAVIGATION")
    return 1


def run_configured_static_asset_diagnostic() -> int:
    """Run one restricted loading observation and render only static aggregate counts."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        result = _run_same_context_browser_course_smoke(
            settings=replace(
                runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
            ),
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_loading_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("STATIC_ASSET_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    if not isinstance(result, CourseIndexLoadingResult):
        print("STATIC_ASSET_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=NAVIGATION")
        return 1
    print("STATIC_ASSET_DIAGNOSTIC")
    for route_class, attempted, allowed, blocked in result.requests.static_counts:
        print(f"{route_class}_ATTEMPTED={attempted}")
        print(f"{route_class}_ALLOWED={allowed}")
        print(f"{route_class}_BLOCKED={blocked}")
    required_static_classes = {
        "THEME_STYLES",
        "MOODLE_CORE_JS",
        "MOODLE_AMD_JS",
        "THEME_JS",
        "THEME_IMAGE",
        "CORE_IMAGE",
        "FONT",
        "OTHER_STATIC_SAME_ORIGIN",
    }
    blocked_any = any(
        route_class in required_static_classes and blocked
        for route_class, _attempted, _allowed, blocked in result.requests.static_counts
    )
    print(
        "STATIC_BOOTSTRAP_STATUS="
        + ("BLOCKED_REQUIRED_ASSETS" if blocked_any else "NO_REQUIRED_STATIC_BLOCK")
    )
    return 0


def render_bootstrap_route_diagnostic(requests: BrowserRequestSummary) -> str:
    """Render only fixed aggregate bootstrap-route observations."""
    lines = ["MOODLE_BOOTSTRAP_ROUTE_DIAGNOSTIC"]
    resource_counts = dict(requests.other_moodle_resource_counts)
    for resource_type in (
        "SCRIPT",
        "STYLESHEET",
        "IMAGE",
        "FONT",
        "XHR_FETCH",
        "DOCUMENT",
        "OTHER",
    ):
        lines.append(f"OTHER_MOODLE_{resource_type}={resource_counts.get(resource_type, 0)}")
    method_counts = dict(requests.other_moodle_method_counts)
    for method in ("GET", "HEAD", "POST"):
        lines.append(f"OTHER_MOODLE_{method}={method_counts.get(method, 0)}")
    lines.append(f"OTHER_MOODLE_OTHER_METHOD={method_counts.get('OTHER', 0)}")
    lines.extend(
        f"OTHER_ORIGIN_{resource_type}={count}"
        for resource_type, count in requests.other_origin_resource_counts
    )
    lines.extend(
        f"{route_class}_ATTEMPTED={count}" for route_class, count in requests.bootstrap_route_counts
    )
    static_classes = {
        "STATIC_LIB_FILE",
        "STATIC_AMD_BUILD_FILE",
        "STATIC_THEME_FILE",
        "THEME_IMAGE_HANDLER",
        "CORE_PIX_ASSET",
        "STATIC_PLUGIN_ASSET",
    }
    status = (
        "SPECIFIC_STATIC_DEPENDENCY_FOUND"
        if any(
            route_class in static_classes for route_class, _count in requests.bootstrap_route_counts
        )
        else "AMBIGUOUS"
        if requests.bootstrap_route_counts
        else "NO_STATIC_DEPENDENCY_FOUND"
    )
    lines.append(f"BOOTSTRAP_ROUTE_STATUS={status}")
    return "\n".join(lines)


def run_configured_moodle_bootstrap_route_diagnostic() -> int:
    """Report fixed structural classes for blocked Moodle bootstrap requests only."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        result = _run_same_context_browser_course_smoke(
            settings=replace(
                runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
            ),
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_loading_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("MOODLE_BOOTSTRAP_ROUTE_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    if not isinstance(result, CourseIndexLoadingResult):
        print("MOODLE_BOOTSTRAP_ROUTE_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=NAVIGATION")
        return 1
    print(render_bootstrap_route_diagnostic(result.requests))
    return 0


def render_moodle_handler_diagnostic(requests: BrowserRequestSummary) -> str:
    """Render fixed handler-family counts and static eligibility only."""
    from aaos.moodle.browser_navigation import handler_static_eligibility

    lines = ["MOODLE_HANDLER_DIAGNOSTIC"]
    counts = {
        (handler_class, resource_type): count
        for handler_class, resource_type, count in requests.moodle_handler_resource_counts
    }
    handler_classes: tuple[MoodleHandlerClass, ...] = (
        "MOODLE_YUI_COMBO_HANDLER",
        "MOODLE_HANDLER_OTHER",
    )
    resource_types: tuple[RequestResourceType, ...] = (
        "SCRIPT",
        "STYLESHEET",
        "FONT",
        "IMAGE",
        "OTHER",
    )
    for handler_class in handler_classes:
        for resource_type in resource_types:
            lines.append(
                f"{handler_class}_{resource_type}={counts.get((handler_class, resource_type), 0)}"
            )
        lines.append(
            f"{handler_class}_STATIC_ELIGIBILITY={handler_static_eligibility(handler_class)}"
        )
    validated_static_script_count = counts.get(("MOODLE_YUI_COMBO_HANDLER", "SCRIPT"), 0)
    validated_static_stylesheet_count = counts.get(("MOODLE_YUI_COMBO_HANDLER", "STYLESHEET"), 0)
    ambiguous_handler_count = sum(
        count
        for (handler_class, _resource_type), count in counts.items()
        if handler_static_eligibility(handler_class) == "AMBIGUOUS"
    )
    lines.extend(
        (
            f"VALIDATED_STATIC_SCRIPT_DEPENDENCY_COUNT={validated_static_script_count}",
            f"VALIDATED_STATIC_STYLESHEET_DEPENDENCY_COUNT={validated_static_stylesheet_count}",
            f"AMBIGUOUS_HANDLER_COUNT={ambiguous_handler_count}",
            "HANDLER_CLASSIFICATION_STATUS="
            + (
                "STATIC_DEPENDENCIES_IDENTIFIED"
                if validated_static_script_count or validated_static_stylesheet_count
                else "AMBIGUOUS"
            ),
        )
    )
    return "\n".join(lines)


def render_final_bootstrap_script_diagnostic(requests: BrowserRequestSummary) -> str:
    """Render the only unresolved Moodle-handler script without request details."""
    ambiguous_script_count = sum(
        count
        for handler_class, resource_type, count in requests.moodle_handler_resource_counts
        if handler_class == "MOODLE_HANDLER_OTHER" and resource_type == "SCRIPT"
    )
    return "\n".join(
        (
            "FINAL_BOOTSTRAP_SCRIPT_DIAGNOSTIC",
            f"AMBIGUOUS_SCRIPT_COUNT={ambiguous_script_count}",
            "SCRIPT_HANDLER_CLASS=MOODLE_HANDLER_OTHER",
            "STATIC_ELIGIBILITY=AMBIGUOUS",
        )
    )


def run_configured_moodle_handler_diagnostic() -> int:
    """Run the restricted observation and render only fixed handler classifications."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        result = _run_same_context_browser_course_smoke(
            settings=replace(
                runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
            ),
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_loading_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("MOODLE_HANDLER_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    if not isinstance(result, CourseIndexLoadingResult):
        print("MOODLE_HANDLER_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=NAVIGATION")
        return 1
    print(render_moodle_handler_diagnostic(result.requests))
    return 0


def run_configured_final_bootstrap_script_diagnostic() -> int:
    """Observe only the unresolved Moodle-handler script with fixed safe output."""
    from dataclasses import replace

    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        config, runtime_settings, paths = load_runtime_moodle_configuration()
        result = _run_same_context_browser_course_smoke(
            settings=replace(
                runtime_settings, source_system="moodle:just:browser-smoke", mode="browser"
            ),
            paths=paths,
            launch_persistent_context=launch_msedge_persistent_context,
            canonical_wwwroot=config.canonical_wwwroot,
            course_index_loading_diagnostic=True,
        )
    except BrowserSmokeFailure as failure:
        print("FINAL_BOOTSTRAP_SCRIPT_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print(f"FAILURE_STAGE={failure.stage}")
        return 1
    if not isinstance(result, CourseIndexLoadingResult):
        print("FINAL_BOOTSTRAP_SCRIPT_DIAGNOSTIC")
        print("DIAGNOSTIC_FAILED")
        print("FAILURE_STAGE=NAVIGATION")
        return 1
    print(render_final_bootstrap_script_diagnostic(result.requests))
    return 0
