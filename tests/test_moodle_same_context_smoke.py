from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from aaos.moodle.collection import CourseDiscovery
from aaos.moodle.models import AuthPaths, MoodleSettings

_ORIGIN = "https://moodle.example.edu"
_COURSE_INDEX = _ORIGIN + "/current/my/courses.php"
_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"
_AUTHENTICATED_PAGE = '<body class="page-mycourses"></body>'
_COURSE_PAGE = '<a href="/current/course/view.php?id=42">redacted</a>'


class _Route:
    def __init__(self) -> None:
        self.allowed = False

    def abort(self) -> None:
        return None

    def continue_(self) -> None:
        self.allowed = True


class _Request:
    method = "POST"
    url = _ORIGIN + "/current/lib/ajax/service.php?redacted"
    resource_type = "xhr"

    def __init__(self, method_name: str = _METHOD) -> None:
        self.post_data = json.dumps(
            [{"index": 0, "methodname": method_name, "args": {"redacted": True}}]
        )


class _Page:
    def __init__(self, context: _Context, kind: str) -> None:
        self._context = context
        self._kind = kind
        self._course_dom_ready = False
        self.url = "about:blank" if kind == "sentinel" else _COURSE_INDEX
        self._handlers: dict[str, object] = {}

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self._context.calls.append(f"{self._kind}-goto")
        assert wait_until == "domcontentloaded"
        if self._kind == "auth":
            assert url == _ORIGIN + "/current/login/index.php"
            assert timeout == 600_000
            return
        assert url == _COURSE_INDEX
        assert timeout == 30_000
        if self._context.unrelated_ajax_method is not None:
            unrelated_route = _Route()
            self._context.request_handler(
                unrelated_route, _Request(self._context.unrelated_ajax_method)
            )
            self._context.calls.append(
                "unrelated-ajax-" + ("allowed" if unrelated_route.allowed else "blocked")
            )
        route = _Route()
        self._context.request_handler(route, _Request(self._context.ajax_method))
        self._course_dom_ready = route.allowed

    def content(self) -> str:
        self._context.calls.append(f"{self._kind}-content")
        if self._kind == "auth":
            return _AUTHENTICATED_PAGE
        if self._kind == "collection" and self._course_dom_ready:
            return _COURSE_PAGE
        return "<body class='page-mycourses'></body>"

    def wait_for_timeout(self, _milliseconds: int) -> None:
        self._context.calls.append(f"{self._kind}-settle")

    def on(self, event: str, _handler: object) -> None:
        self._handlers[event] = _handler
        self._context.calls.append(f"{self._kind}-{event}")

    def close(self) -> None:
        self._context.calls.append(f"{self._kind}-close")
        self._context.pages.remove(self)


class _Context:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self.pages: list[_Page] = []
        self.calls: list[str] = []
        self.request_handler: Any = lambda *_args: None
        self.interception_installed = False
        self.ajax_method = _METHOD
        self.unrelated_ajax_method: str | None = None
        self.closed_with_lock = False

    def new_page(self) -> _Page:
        if self.interception_installed:
            kind = "collection"
        elif any(page._kind == "auth" for page in self.pages):
            kind = "sentinel"
        else:
            kind = "auth"
        page = _Page(self, kind)
        self.pages.append(page)
        self.calls.append(f"{kind}-new")
        return page

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        assert len(self.pages) == 1 and self.pages[0]._kind == "sentinel"
        self.interception_installed = True
        self.request_handler = cast(Any, handler)
        self.calls.append("interception")

    def close(self) -> None:
        self.closed_with_lock = self._lock_path.exists()
        self.calls.append("context-close")
        self.pages.clear()


def _settings() -> MoodleSettings:
    return MoodleSettings("moodle:fixture:browser-smoke", _ORIGIN, "browser", "fixture")


def _paths(tmp_path: Path) -> AuthPaths:
    return AuthPaths(tmp_path / "credential", tmp_path / "auth" / "fixture")


def test_same_context_smoke_uses_moodle_frontend_dom_after_sentinel_transition(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")

    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert context.calls.index("sentinel-new") < context.calls.index("auth-close")
    assert context.calls.index("auth-close") < context.calls.index("interception")
    assert context.calls.index("interception") < context.calls.index("collection-new")
    assert context.calls.index("collection-new") < context.calls.index("sentinel-close")
    assert context.closed_with_lock is True
    assert context.calls[-1] == "context-close"


def test_same_context_smoke_blocks_unrelated_ajax_without_failing_course_discovery(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import _run_same_context_browser_course_smoke

    paths = _paths(tmp_path)
    context = _Context(paths.browser_state_dir / ".playwright-edge-profile.lock")
    context.unrelated_ajax_method = "core_user_update"

    result = _run_same_context_browser_course_smoke(
        settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
    )

    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert "unrelated-ajax-blocked" in context.calls
    assert context.closed_with_lock is True

def test_same_context_smoke_closes_context_before_releasing_profile_lock_on_failure(
    tmp_path: Path,
) -> None:
    from aaos.moodle.browser import BrowserSmokeFailure, _run_same_context_browser_course_smoke

    class _BrokenContext(_Context):
        def new_page(self) -> _Page:
            if self.interception_installed:
                raise OSError
            return super().new_page()

    paths = _paths(tmp_path)
    context = _BrokenContext(paths.browser_state_dir / ".playwright-edge-profile.lock")
    with pytest.raises(BrowserSmokeFailure) as failure:
        _run_same_context_browser_course_smoke(
            settings=_settings(), paths=paths, launch_persistent_context=lambda **_: context
        )

    assert failure.value.stage == "COLLECTION_PAGE_CREATE"
    assert context.closed_with_lock is True


def test_browser_smoke_cli_renders_only_course_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import aaos.moodle.browser as browser

    monkeypatch.setattr(
        browser,
        "_run_same_context_browser_course_smoke",
        lambda **_kwargs: CourseDiscovery((), is_complete=True),
    )

    assert browser.run_configured_browser_course_smoke() == 0
    assert capsys.readouterr().out == "COURSE_DISCOVERY_PASS\nCOURSE_COUNT=0\n"


def test_rejected_custom_session_transport_is_not_present_in_production() -> None:
    root = Path(__file__).parents[1] / "src" / "aaos" / "moodle"
    assert not (root / "session_read.py").exists()
    production = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("browser.py", "browser_navigation.py", "browser_parsing.py")
    )
    assert "provenance" not in production.lower()
    assert "_fixed_bridge" not in production.lower()
    assert "page.evaluate" not in production
