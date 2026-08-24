from __future__ import annotations

from pathlib import Path

import pytest

from aaos.academic.source import SourceCollectionFailure
from aaos.moodle.browser_models import BrowserPolicyError
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
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    lock_path = paths.browser_state_dir / ".playwright-edge-profile.lock"
    context = _Context(lock_path, auth_survivor=True)

    with pytest.raises(BrowserPolicyError):
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert "interception" not in context.calls
    assert "collection-new" not in context.calls
    assert context.closed_with_lock is True
    assert lock_path.exists() is False


def test_same_context_smoke_does_not_collect_after_auth_timeout(tmp_path: Path) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

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

    with pytest.raises(TimeoutError):
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert "interception" not in context.calls
    assert "collection-new" not in context.calls
    assert context.closed_with_lock is True


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
