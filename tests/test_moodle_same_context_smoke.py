from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from playwright._impl._errors import TargetClosedError
from playwright.sync_api import Error as PlaywrightError

from aaos.academic.source import SourceCollectionFailure
from aaos.moodle.collection import CourseDiscovery
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.session_read import SessionCourseReadResult

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
        self.url = "about:blank" if kind == "sentinel" else _COURSE_INDEX
        self._handlers: dict[str, object] = {}

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
        self._context.calls.append(f"{self._kind}-content")
        assert self._kind != "sentinel"
        return _AUTHENTICATED_PAGE if self._kind == "auth" else _COURSE_PAGE

    def wait_for_timeout(self, _milliseconds: int) -> None:
        self._context.calls.append(f"{self._kind}-settle")

    def on(self, event: str, handler: object) -> None:
        self._handlers[event] = handler
        self._context.calls.append(f"{self._kind}-{event}")

    def evaluate(self, expression: str, arg: object) -> object:
        self._context.calls.append(f"{self._kind}-evaluate")
        assert self._kind == "collection"
        assert "core_course_get_enrolled_courses_by_timeline_classification" in expression
        offset = arg["offset"] if isinstance(arg, dict) else None
        body = (
            '[{"index":0,"methodname":"core_course_get_enrolled_courses_by_timeline_classification",'
            '"args":{"classification":"allincludinghidden","limit":100,"offset":'
            + str(offset)
            + ',"requiredfields":["id"]}}]'
        )
        self._context.request_handler(
            SimpleNamespace(continue_=lambda: None, abort=lambda: None),
            SimpleNamespace(
                method="POST",
                url=(
                    "https://moodle.example.edu/current/lib/ajax/service.php?"
                    "sesskey=opaque&info=core_course_get_enrolled_courses_by_timeline_classification&"
                    "nosessionupdate=true"
                ),
                resource_type="xhr",
                post_data=body,
            ),
        )
        response_handler = self._handlers.get("response")
        if callable(response_handler):
            response_handler(
                SimpleNamespace(
                    status=self._context.ajax_response_status,
                    url="https://moodle.example.edu/current/lib/ajax/service.php",
                )
            )
        if arg == {"offset": 0}:
            return {"course_ids": [42], "next_offset": 1}
        assert arg == {"offset": 1}
        return {"course_ids": [], "next_offset": 1}

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
        self.ajax_response_status = 200
        self.request_handler: Callable[[object, object], None] = lambda *_args: None
        self.closed_with_lock = False

    def new_page(self) -> _Page:
        if self.interception_installed:
            kind = "collection"
        elif any(page._kind == "auth" for page in self.pages):
            kind = "sentinel"
        else:
            kind = "auth"
        page = _Page(self, kind, survive_close=kind == "auth" and self._auth_survivor)
        self.pages.append(page)
        self.calls.append(f"{kind}-new")
        return page

    def route(self, pattern: str, _handler: object) -> None:
        assert pattern == "**/*"
        assert len(self.pages) == 1
        assert self.pages[0]._kind == "sentinel"
        assert self.pages[0].url == "about:blank"
        self.interception_installed = True
        self.request_handler = cast(Callable[[object, object], None], _handler)
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

    assert isinstance(result, SessionCourseReadResult)
    assert result.course_ids == (42,)
    assert context.calls.index("sentinel-new") < context.calls.index("auth-close")
    assert context.calls.index("auth-close") < context.calls.index("interception")
    assert context.calls.index("interception") < context.calls.index("collection-new")
    assert context.calls.index("collection-new") < context.calls.index("sentinel-close")
    assert context.calls.index("sentinel-close") < context.calls.index("collection-goto")
    assert "sentinel-goto" not in context.calls
    assert "sentinel-content" not in context.calls
    assert context.closed_with_lock is True
    assert context.calls[-1] == "context-close"
    assert lock_path.exists() is False


def test_same_context_smoke_fails_closed_on_ajax_redirect(tmp_path: Path) -> None:
    from aaos.moodle.browser import BrowserSmokeFailure, _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")
    context.ajax_response_status = 302

    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "SESSION_READ"
    assert "collection-response" in context.calls


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

    assert isinstance(result, SessionCourseReadResult)
    assert "auth-popup-close" in context.calls
    assert context.calls.index("auth-popup-close") < context.calls.index("interception")


def test_same_context_smoke_rejects_unexpected_page_created_with_sentinel(tmp_path: Path) -> None:
    from aaos.moodle.browser import BrowserSmokeFailure, _run_same_context_browser_course_smoke

    class _ExtraPageContext(_Context):
        def new_page(self) -> _Page:
            page = super().new_page()
            if page._kind == "sentinel":
                self.pages.append(_Page(self, "unexpected"))
            return page

    paths = _paths(tmp_path)
    context = _ExtraPageContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "TRANSITION_PAGES_REMAIN"
    assert "interception" not in context.calls


def test_same_context_smoke_rejects_sentinel_url_mutation_before_interception(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _MutatedSentinelContext(_Context):
        def new_page(self) -> _Page:
            page = super().new_page()
            if page._kind == "sentinel":
                page.url = "https://moodle.example.edu/current/my/courses.php"
            return page

    paths = _paths(tmp_path)
    context = _MutatedSentinelContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "TRANSITION_PAGES_REMAIN"
    assert "interception" not in context.calls


def test_same_context_smoke_rejects_sentinel_url_mutation_before_collection_navigation(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _LateMutatedSentinelContext(_Context):
        def new_page(self) -> _Page:
            page = super().new_page()
            if page._kind == "collection":
                sentinel = next(item for item in self.pages if item._kind == "sentinel")
                sentinel.url = "https://moodle.example.edu/current/my/courses.php"
            return page

    paths = _paths(tmp_path)
    context = _LateMutatedSentinelContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "SESSION_READ"
    assert "collection-goto" not in context.calls


def test_same_context_smoke_rejects_sentinel_replacement_before_interception(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _ReplacingAuthPage(_Page):
        def close(self) -> None:
            self._context.calls.append("auth-close")
            self._context.pages.remove(self)
            self._context.pages[:] = [_Page(self._context, "sentinel")]

    class _ReplacementContext(_Context):
        def new_page(self) -> _Page:
            if not any(page._kind == "auth" for page in self.pages):
                page = _ReplacingAuthPage(self, "auth")
                self.pages.append(page)
                self.calls.append("auth-new")
                return page
            return super().new_page()

    paths = _paths(tmp_path)
    context = _ReplacementContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "TRANSITION_PAGES_REMAIN"
    assert "interception" not in context.calls


def test_same_context_smoke_fails_closed_when_sentinel_creation_fails(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _SentinelCreationFailureContext(_Context):
        def new_page(self) -> _Page:
            if any(page._kind == "auth" for page in self.pages) and not self.interception_installed:
                raise OSError
            return super().new_page()

    paths = _paths(tmp_path)
    context = _SentinelCreationFailureContext(
        paths.browser_state_dir / ".playwright-edge-profile.lock"
    )
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "AUTH_PAGE_CLEANUP"
    assert "interception" not in context.calls


def test_same_context_smoke_rejects_sentinel_close_failure_before_navigation(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _ClosingFailureSentinel(_Page):
        def close(self) -> None:
            if self._kind == "sentinel":
                raise OSError
            super().close()

    class _ClosingFailureContext(_Context):
        def new_page(self) -> _Page:
            if not self.interception_installed and any(page._kind == "auth" for page in self.pages):
                page = _ClosingFailureSentinel(self, "sentinel")
                self.pages.append(page)
                self.calls.append("sentinel-new")
                return page
            return super().new_page()

    paths = _paths(tmp_path)
    context = _ClosingFailureContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "SESSION_READ"
    assert "collection-goto" not in context.calls


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
            if not any(page._kind == "auth" for page in self.pages):
                page = _ClosingPage(self, "auth")
                self.pages.append(page)
                self.calls.append("auth-new")
                return page
            return super().new_page()

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

    assert failure.value.stage == "SESSION_READ"


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

    assert failure.value.stage == "SESSION_READ"


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

    assert failure.value.stage in {"SESSION_READ", "CLEANUP"}


def test_same_context_smoke_does_not_use_ui_parser_for_production_read(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        _run_same_context_browser_course_smoke,
    )

    class _MalformedCollectionPage(_Page):
        def content(self) -> str:
            if self._kind == "collection":
                return "<html></html>"
            return super().content()

    class _MalformedCollectionContext(_Context):
        def new_page(self) -> _Page:
            if self.interception_installed:
                kind = "collection"
            elif any(page._kind == "auth" for page in self.pages):
                kind = "sentinel"
            else:
                kind = "auth"
            page = _MalformedCollectionPage(self, kind)
            self.pages.append(page)
            self.calls.append(f"{kind}-new")
            return page

    paths = _paths(tmp_path)
    context = _MalformedCollectionContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )
    assert isinstance(result, SessionCourseReadResult)


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


def test_same_context_smoke_keeps_result_when_context_already_closed_after_collection(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    class _AlreadyClosedContext(_Context):
        def close(self) -> None:
            self.pages.clear()
            raise TargetClosedError("redacted")

    paths = _paths(tmp_path)
    result = _run_same_context_browser_course_smoke(
        settings=_settings(),
        paths=paths,
        launch_persistent_context=lambda **_: _AlreadyClosedContext(
            paths.browser_state_dir / ".playwright-edge-profile.lock"
        ),
    )

    assert isinstance(result, SessionCourseReadResult)


def test_same_context_smoke_keeps_generic_playwright_cleanup_failure(tmp_path: Path) -> None:
    from aaos.moodle.browser import (
        BrowserSmokeFailure,
        _run_same_context_browser_course_smoke,
    )

    class _GenericPlaywrightFailureContext(_Context):
        def close(self) -> None:
            self.pages.clear()
            raise PlaywrightError("redacted")

    paths = _paths(tmp_path)
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(),
            paths=paths,
            launch_persistent_context=lambda **_: _GenericPlaywrightFailureContext(
                paths.browser_state_dir / ".playwright-edge-profile.lock"
            ),
        )

    assert failure.value.stage == "CLEANUP"


def test_same_context_smoke_does_not_instantiate_ui_source_for_production_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.moodle.browser as browser

    class _FailingSource:
        def __init__(self, *_args: object) -> None:
            return None

        def discover_courses(self) -> SourceCollectionFailure:
            raise AssertionError("UI source must not be used")

    monkeypatch.setattr(browser, "MoodleBrowserSource", _FailingSource)
    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")
    result = browser._run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )
    assert isinstance(result, SessionCourseReadResult)


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
        lambda: (
            SimpleNamespace(canonical_wwwroot=_ORIGIN + "/current"),
            runtime_settings,
            _paths(tmp_path),
        ),
    )
    captured: list[MoodleSettings] = []

    def run_smoke(**kwargs: object) -> SessionCourseReadResult:
        settings = kwargs["settings"]
        assert isinstance(settings, MoodleSettings)
        captured.append(settings)
        return SessionCourseReadResult(())

    monkeypatch.setattr(browser, "_run_same_context_browser_course_smoke", run_smoke)

    assert browser.run_configured_browser_course_smoke() == 0
    assert captured == [MoodleSettings("moodle:just:browser-smoke", _ORIGIN, "browser", "fixture")]


def test_course_index_structure_diagnostic_uses_same_browser_smoke_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aaos.moodle.browser as browser
    import aaos.moodle.receiver as receiver
    from aaos.moodle.browser_parsing import CourseIndexStructureDiagnostic

    runtime_settings = MoodleSettings("moodle:fixture:bootstrap", _ORIGIN, "api", "fixture")
    monkeypatch.setattr(
        receiver,
        "load_runtime_moodle_configuration",
        lambda: (
            SimpleNamespace(canonical_wwwroot=_ORIGIN + "/current"),
            runtime_settings,
            _paths(tmp_path),
        ),
    )
    captured: list[object] = []

    def run_diagnostic(**kwargs: object) -> CourseIndexStructureDiagnostic:
        captured.append(kwargs["course_index_diagnostic"])
        return CourseIndexStructureDiagnostic(
            page_identity="MY_COURSES",
            course_container_count=0,
            candidate_course_link_count=0,
            numeric_course_id_link_count=0,
            course_data_attribute_count=0,
            pagination_container_count=0,
            next_page_link_count=0,
            empty_state_marker=True,
            known_fixture_schema_match=False,
            course_link_shape="NONE",
        )

    monkeypatch.setattr(browser, "_run_same_context_browser_course_smoke", run_diagnostic)

    assert browser.run_configured_course_index_structure_diagnostic() == 0
    assert captured == [True]
    assert capsys.readouterr().out.splitlines() == [
        "COURSE_INDEX_STRUCTURE_DIAGNOSTIC",
        "PAGE_IDENTITY=MY_COURSES",
        "COURSE_CONTAINER_COUNT=0",
        "CANDIDATE_COURSE_LINK_COUNT=0",
        "NUMERIC_COURSE_ID_LINK_COUNT=0",
        "COURSE_DATA_ATTRIBUTE_COUNT=0",
        "PAGINATION_CONTAINER_COUNT=0",
        "NEXT_PAGE_LINK_COUNT=0",
        "EMPTY_STATE_MARKER=YES",
        "KNOWN_FIXTURE_SCHEMA_MATCH=NO",
        "COURSE_LINK_SHAPE=NONE",
    ]


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

    assert isinstance(result, SessionCourseReadResult)


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

    assert isinstance(result, SessionCourseReadResult)
    assert not isinstance(result, SourceCollectionFailure)
