from __future__ import annotations

from typing import Any, cast

import pytest

from aaos.moodle.browser_models import BrowserPolicyError

_ORIGIN = "https://moodle.example.edu"
_COURSE_INDEX = _ORIGIN + "/current/my/courses.php"


class _Route:
    def abort(self) -> None:
        return None

    def continue_(self) -> None:
        return None


class _Page:
    def __init__(self, context: "_Context", *, sentinel: bool) -> None:
        self._context = context
        self.url = "about:blank" if sentinel else _COURSE_INDEX

    def on(self, event: str, handler: object) -> None:
        # Intentionally never emit a Moodle course AJAX response.
        del event, handler

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        assert url == _COURSE_INDEX
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.url = url

    def content(self) -> str:
        # This is deliberately sufficient for the old DOM fallback.
        return (
            '<body class="page-mycourses">'
            '<a href="/current/course/view.php?id=42">course</a>'
            "</body>"
        )

    def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds > 0

    def close(self) -> None:
        if self in self._context.pages:
            self._context.pages.remove(self)


class _Context:
    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self.request_handler: Any = lambda *_args: None

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        self.request_handler = cast(Any, handler)

    def new_page(self) -> _Page:
        page = _Page(self, sentinel=False)
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.pages.clear()


def test_frontend_course_discovery_rejects_dom_only_without_validated_course_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.moodle.browser_navigation as navigation

    ticks = iter(range(1000))
    monkeypatch.setattr(navigation, "monotonic", lambda: next(ticks))

    context = _Context()
    sentinel = _Page(context, sentinel=True)
    context.pages.append(sentinel)

    navigator = navigation.PlaywrightRestrictedNavigator(
        cast(Any, context),
        _ORIGIN,
        canonical_wwwroot=_ORIGIN + "/current",
        transition_sentinel=cast(Any, sentinel),
    )

    with pytest.raises(BrowserPolicyError):
        navigator.open_frontend_course_index()

    assert navigator.failure_stage == "NAVIGATION"
    assert len(context.pages) == 1
    assert context.pages[0] is sentinel
    assert sentinel.url == "about:blank"
