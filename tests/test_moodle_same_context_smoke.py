from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from aaos.academic.source import SourceCollectionFailure
from aaos.moodle.collection import CourseDiscovery
from aaos.moodle.models import AuthPaths, MoodleSettings

_ORIGIN = "https://moodle.example.edu"
_COURSE_INDEX = _ORIGIN + "/current/my/courses.php"
_AUTHENTICATED_PAGE = '<body class="page-mycourses pagelayout-mycourses"></body>'
_COURSE_PAGE = (
    '<a data-moodle-course="true" '
    'href="https://moodle.example.edu/current/course/view.php?id=42">Synthetic</a>'
    '<nav data-pagination-pages="0" data-pagination-exhausted="true"></nav>'
)


class _Page:
    def __init__(self, context: _Context, kind: str, *, survive_close: bool = False) -> None:
        self._context = context
        self._kind = kind
        self._survive_close = survive_close
        self.url = _COURSE_INDEX

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self._context.calls.append(f"{self._kind}-goto")
        assert wait_until == "domcontentloaded"
        if self._kind == "auth":
            assert url == _ORIGIN + "/current/login/index.php"
            assert timeout == 600_000
        else:
            assert url == _COURSE_INDEX
            assert timeout == 30_000

    def content(self) -> str:
        return _AUTHENTICATED_PAGE if self._kind == "auth" else _COURSE_PAGE

    def wait_for_timeout(self, _milliseconds: int) -> None:
        self._context.calls.append(f"{self._kind}-settle")

    def on(self, event: str, _handler: object) -> None:
        self._context.calls.append(f"{self._kind}-{event}")

    def close(self) -> None:
        self._context.calls.append(f"{self._kind}-close")
        if not self._survive_close:
            self._context.pages.remove(self)


class _Context:
    def __init__(self, lock_path: Path, *, auth_survivor: bool = False) -> None:
        self._lock_path = lock_path
        self._auth_survivor = auth_survivor
        self.pages: list[_Page] = []
        self.calls: list[str] = []
        self.interception_installed = False
        self.closed_with_lock = False

    def new_page(self) -> _Page:
        kind = "collection" if self.interception_installed else "auth"
        page = _Page(self, kind, survive_close=kind == "auth" and self._auth_survivor)
        self.pages.append(page)
        self.calls.append(f"{kind}-new")
        return page

    def route(self, pattern: str, _handler: object) -> None:
        assert pattern == "**/*"
        assert self.pages == []
        self.interception_installed = True
        self.calls.append("interception")

    def close(self) -> None:
        self.closed_with_lock = self._lock_path.exists()
        self.calls.append("context-close")
        self.pages.clear()


def _settings() -> MoodleSettings:
    return MoodleSettings("moodle:fixture:browser-smoke", _ORIGIN, "browser", "fixture")


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(tmp_path / "credential", tmp_path / "auth" / "fixture")


def test_same_context_smoke_authenticates_then_collects_after_page_transition(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _Context(lock_path)

    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert context.calls.index("auth-close") < context.calls.index("interception")
    assert context.calls.index("interception") < context.calls.index("collection-new")
    assert context.closed_with_lock is True
    assert context.calls[-1] == "context-close"
    assert lock_path.exists() is False


def test_same_context_smoke_closes_auth_popups_before_installing_interception(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _Context(lock_path)
    popup = _Page(context, "auth-popup")
    context.pages.append(popup)

    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)
    assert "auth-popup-close" in context.calls
    assert context.calls.index("auth-popup-close") < context.calls.index("interception")


def test_same_context_smoke_fails_closed_when_auth_page_survives_transition(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _Context(lock_path, auth_survivor=True)

    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "TRANSITION_PAGES_REMAIN"
    assert "interception" not in context.calls
    assert "collection-new" not in context.calls
    assert context.closed_with_lock is True
    assert lock_path.exists() is False


def test_same_context_smoke_does_not_collect_after_auth_timeout(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _TimedOutPage(_Page):
        def wait_for_timeout(self, _milliseconds: int) -> None:
            raise TimeoutError

    class _TimedOutContext(_Context):
        def new_page(self) -> _Page:
            page = _TimedOutPage(self, "auth")
            self.pages.append(page)
            self.calls.append("auth-new")
            return page

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _TimedOutContext(lock_path)

    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "AUTH_TIMEOUT"
    assert "interception" not in context.calls
    assert "collection-new" not in context.calls
    assert context.closed_with_lock is True


def test_same_context_smoke_classifies_missing_authenticated_page_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.browser as browser

    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")
    monkeypatch.setattr(browser, "wait_for_authenticated_course_index", lambda **_kwargs: False)
    with pytest.raises(browser.BrowserSmokeFailure) as failure:
        browser._run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "AUTH_PROOF"
    assert "interception" not in context.calls


def test_same_context_smoke_classifies_busy_profile_lock(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )
    from aaos.moodle.browser_models import (
        acquire_browser_profile_lock,
        resolve_browser_profile_paths,
    )

    paths = _paths(tmp_path)
    profile = resolve_browser_profile_paths(paths, _ORIGIN)
    with acquire_browser_profile_lock(profile):
        with pytest.raises(BrowserSmokeFailure) as failure:
            _run_same_context_browser_course_smoke(
                settings=_settings(),
                paths=paths,
                launch_persistent_context=lambda **_: _Context(profile.lock_path),
            )

    assert failure.value.stage == "PROFILE"


def test_same_context_smoke_classifies_auth_page_cleanup_failure(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _ClosingPage(_Page):
        def close(self) -> None:
            raise OSError

    class _ClosingContext(_Context):
        def new_page(self) -> _Page:
            page = _ClosingPage(self, "auth")
            self.pages.append(page)
            self.calls.append("auth-new")
            return page

    context = _ClosingContext(_paths(tmp_path).browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(),
            paths=_paths(tmp_path),
            launch_persistent_context=lambda **_: context,
        )

    assert failure.value.stage == "AUTH_PAGE_CLEANUP"
    assert "interception" not in context.calls


def test_same_context_smoke_classifies_interception_install_failure(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _InterceptionFailureContext(_Context):
        def route(self, _pattern: str, _handler: object) -> None:
            raise OSError

    paths = _paths(tmp_path)
    context = _InterceptionFailureContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "INTERCEPTION_INSTALL"


def test_same_context_smoke_classifies_collection_page_creation_failure(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _CollectionCreationFailureContext(_Context):
        def new_page(self) -> _Page:
            if self.interception_installed:
                raise OSError
            return super().new_page()

    paths = _paths(tmp_path)
    context = _CollectionCreationFailureContext(
        paths.browser_state_dir / ".playwright-edge-profile.lock"
    )
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "COLLECTION_PAGE_CREATE"


def test_same_context_smoke_preserves_collection_page_failure_when_context_is_closed(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _ClosedContext(_Context):
        def new_page(self) -> _Page:
            if self.interception_installed:
                raise PlaywrightError("redacted")
            return super().new_page()

        def close(self) -> None:
            super().close()
            raise PlaywrightError("redacted")

    paths = _paths(tmp_path)
    context = _ClosedContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "COLLECTION_PAGE_CREATE"


def test_same_context_smoke_classifies_parser_failure(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _MalformedCollectionPage(_Page):
        def content(self) -> str:
            if self._kind == "collection":
                return "<html></html>"
            return super().content()

    class _MalformedCollectionContext(_Context):
        def new_page(self) -> _Page:
            kind = "collection" if self.interception_installed else "auth"
            page = _MalformedCollectionPage(self, kind)
            self.pages.append(page)
            self.calls.append(f"{kind}-new")
            return page

    paths = _paths(tmp_path)
    context = _MalformedCollectionContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "PARSER_SCHEMA"


def test_same_context_smoke_classifies_cleanup_failure_after_collection(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _CleanupFailureContext(_Context):
        def close(self) -> None:
            super().close()
            raise OSError

    paths = _paths(tmp_path)
    context = _CleanupFailureContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "CLEANUP"


@pytest.mark.parametrize(
    ("failure_code", "expected_stage"),
    [
        ("browser_request_policy", "REQUEST_POLICY"),
        ("browser_unexpected_redirect", "UNEXPECTED_REDIRECT"),
    ],
)
def test_same_context_smoke_preserves_restricted_navigation_failure_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_code: str,
    expected_stage: str,
) -> None:
    import aaos.moodle.browser as browser

    class _FailingSource:
        def __init__(self, *_args: object) -> None:
            return None

        def discover_courses(self) -> SourceCollectionFailure:
            return SourceCollectionFailure("moodle:fixture:browser-smoke", failure_code)

    monkeypatch.setattr(browser, "MoodleBrowserSource", _FailingSource)
    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(browser.BrowserSmokeFailure) as failure:
        browser._run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == expected_stage


def test_browser_smoke_cli_renders_only_fixed_failure_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aaos.moodle.browser as browser

    def fail(**_kwargs: object) -> CourseDiscovery:
        raise browser.BrowserSmokeFailure("AUTH_PROOF")

    monkeypatch.setattr(browser, "_run_same_context_browser_course_smoke", fail)

    assert browser.run_configured_browser_course_smoke() == 1
    assert capsys.readouterr().out == "COURSE_DISCOVERY_FAILED\nFAILURE_STAGE=AUTH_PROOF\n"


def test_browser_smoke_cli_derives_browser_mode_from_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aaos.moodle.browser as browser
    import aaos.moodle.receiver as receiver

    runtime_settings = MoodleSettings("moodle:fixture:bootstrap", _ORIGIN, "api", "fixture")
    monkeypatch.setattr(
        receiver,
        "load_runtime_moodle_configuration",
        lambda: (object(), runtime_settings, _paths(tmp_path)),
    )
    captured: list[MoodleSettings] = []

    def run_smoke(**kwargs: object) -> CourseDiscovery:
        settings = kwargs["settings"]
        assert isinstance(settings, MoodleSettings)
        captured.append(settings)
        return CourseDiscovery((), is_complete=True)

    monkeypatch.setattr(browser, "_run_same_context_browser_course_smoke", run_smoke)

    assert browser.run_configured_browser_course_smoke() == 0
    assert captured == [MoodleSettings("moodle:just:browser-smoke", _ORIGIN, "browser", "fixture")]


def test_same_context_smoke_accepts_existing_profile_binding_used_by_session_diagnostic(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke
    from aaos.moodle.browser_models import (
        ensure_browser_profile_binding,
        resolve_browser_profile_paths,
        validate_browser_profile_binding,
    )

    paths = _paths(tmp_path)
    settings = _settings()
    profile = resolve_browser_profile_paths(paths, settings.base_origin)
    ensure_browser_profile_binding(
        profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
    )
    validate_browser_profile_binding(
        profile, expected_origin=settings.base_origin, expected_alias=settings.auth_alias
    )
    context = _Context(profile.lock_path)

    result = _run_same_context_browser_course_smoke(
        settings=settings, paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)


def test_same_context_smoke_returns_only_course_discovery_not_snapshot_or_entities(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _Context(lock_path)

    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)
    assert not isinstance(result, SourceCollectionFailure)
