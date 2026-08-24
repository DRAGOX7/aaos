"""Structural, non-sensitive persistent-browser session lifetime diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Literal, Protocol, cast

from playwright.sync_api import Error as PlaywrightError

from aaos.moodle.browser_parsing import has_authenticated_my_courses_page

SameContextSession = Literal["AUTHENTICATED", "NOT_AUTHENTICATED"]
RelaunchedSession = Literal["AUTHENTICATED", "LOGIN_ROUTE", "OTHER_FAILURE"]


class _CourseIndexPage(Protocol):
    url: str

    def content(self) -> str: ...

    def wait_for_timeout(self, milliseconds: int) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionLifetimeReport:
    same_context: SameContextSession
    relaunched: RelaunchedSession

    @property
    def result(self) -> str:
        if self.same_context == "AUTHENTICATED" and self.relaunched == "LOGIN_ROUTE":
            return "PROCESS_BOUND_SESSION_CONFIRMED"
        if self.same_context == "AUTHENTICATED" and self.relaunched == "AUTHENTICATED":
            return "PERSISTENCE_WORKS"
        return "INCONCLUSIVE"


def render_session_lifetime_report(report: SessionLifetimeReport) -> str:
    """Render only fixed diagnostic values, never browser or Moodle content."""
    return "\n".join(
        (
            "SESSION_LIFETIME_DIAGNOSTIC",
            f"SAME_CONTEXT_SESSION={report.same_context}",
            f"RELAUNCHED_SESSION={report.relaunched}",
            "PROFILE_BINDING_MATCH=PASS",
            "CLEAN_RELAUNCH=PASS",
            f"SESSION_LIFETIME_RESULT={report.result}",
        )
    )


def wait_for_authenticated_course_index(
    page: _CourseIndexPage,
    *,
    course_index_url: str,
    timeout_seconds: int,
    monotonic: Callable[[], float] = monotonic,
    poll_milliseconds: int = 250,
) -> bool:
    """Wait for the fixed course route and its structural authenticated marker."""
    deadline = monotonic() + timeout_seconds
    try:
        while True:
            if page.url == course_index_url and has_authenticated_my_courses_page(page.content()):
                remaining_seconds = deadline - monotonic()
                if remaining_seconds <= 0:
                    return False
                page.wait_for_timeout(min(poll_milliseconds, max(1, int(remaining_seconds * 1000))))
                if page.url == course_index_url and has_authenticated_my_courses_page(
                    page.content()
                ):
                    return True
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                return False
            page.wait_for_timeout(min(poll_milliseconds, max(1, int(remaining_seconds * 1000))))
    except PlaywrightError:
        return False


def run_configured_session_lifetime_diagnostic() -> int:
    """Run two bounded lifetimes of one profile; return fixed structural evidence only."""
    from aaos.moodle.browser_models import (
        BrowserPolicyError,
        acquire_browser_profile_lock,
        resolve_browser_profile_paths,
        validate_browser_profile_binding,
    )
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator
    from aaos.moodle.browser_runtime import launch_msedge_persistent_context
    from aaos.moodle.receiver import load_runtime_moodle_configuration

    try:
        _config, settings, paths = load_runtime_moodle_configuration()
        profile = resolve_browser_profile_paths(paths, settings.base_origin)
        with acquire_browser_profile_lock(profile):
            validate_browser_profile_binding(
                profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
            )
            first = launch_msedge_persistent_context(
                channel="msedge",
                headless=False,
                user_data_dir=profile.profile_dir,
                accept_downloads=False,
                service_workers="block",
            )
            try:
                page = cast(Any, first).new_page()
                page.goto(
                    settings.base_origin + "/current/login/index.php",
                    wait_until="domcontentloaded",
                    timeout=600000,
                )
                same: SameContextSession = (
                    "AUTHENTICATED"
                    if wait_for_authenticated_course_index(
                        page,
                        course_index_url=settings.base_origin + "/current/my/courses.php",
                        timeout_seconds=600,
                    )
                    else "NOT_AUTHENTICATED"
                )
            finally:
                first.close()
        with acquire_browser_profile_lock(profile):
            validate_browser_profile_binding(
                profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
            )
            second = launch_msedge_persistent_context(
                channel="msedge",
                headless=False,
                user_data_dir=profile.profile_dir,
                accept_downloads=False,
                service_workers="block",
            )
            navigator = PlaywrightRestrictedNavigator(cast(Any, second), settings.base_origin)
            try:
                navigator.open_course_index_document()
                relaunched: RelaunchedSession = "AUTHENTICATED"
            except BrowserPolicyError:
                relaunched = (
                    "LOGIN_ROUTE" if navigator.redirect_class == "LOGIN_ROUTE" else "OTHER_FAILURE"
                )
            finally:
                navigator.close()
    except (OSError, ValueError, TypeError, BrowserPolicyError, TimeoutError):
        report = SessionLifetimeReport("NOT_AUTHENTICATED", "OTHER_FAILURE")
    else:
        report = SessionLifetimeReport(same, relaunched)
    print(render_session_lifetime_report(report))
    return 0 if report.result != "INCONCLUSIVE" else 1
