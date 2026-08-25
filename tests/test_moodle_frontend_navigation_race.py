from __future__ import annotations

import json
from typing import Any, cast

from playwright.sync_api import Error as PlaywrightError

from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

_ORIGIN = "https://moodle.example.edu"
_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"


class _CourseRequest:
    method = "POST"
    resource_type = "fetch"
    url = _ORIGIN + "/current/lib/ajax/service.php?redacted"
    post_data = json.dumps(
        [
            {
                "index": 0,
                "methodname": _METHOD,
                "args": {"offset": 0, "limit": 0},
            }
        ]
    )


class _CourseResponse:
    status = 200
    request = _CourseRequest()

    def json(self) -> object:
        return [
            {
                "error": False,
                "data": {
                    "courses": [{"id": 42}],
                    "nextoffset": 1,
                },
            }
        ]


class _Page:
    def __init__(self, context: "_Context", *, sentinel: bool) -> None:
        self._context = context
        self.url = "about:blank"
        self._sentinel = sentinel
        self._content_calls = 0
        self._handlers: dict[str, Any] = {}
        self._response_emitted = False

    def on(self, event: str, handler: object) -> None:
        self._handlers[event] = cast(Any, handler)

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.url = url

    def content(self) -> str:
        if self._sentinel:
            return "<html></html>"
        self._content_calls += 1
        if self._content_calls == 1:
            raise PlaywrightError(
                "Page.content: Unable to retrieve content because "
                "the page is navigating and changing the content."
            )
        # DOM rendering is deliberately irrelevant to course discovery now.
        return "<body class='page-mycourses'></body>"

    def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds > 0
        if self._sentinel or self._response_emitted:
            return
        self._response_emitted = True
        response_handler = self._handlers.get("response")
        if response_handler is not None:
            response_handler(_CourseResponse())

    def close(self) -> None:
        if self in self._context.pages:
            self._context.pages.remove(self)


class _Context:
    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self._created = 0

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        del handler

    def new_page(self) -> _Page:
        self._created += 1
        page = _Page(self, sentinel=self._created == 1)
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.pages.clear()


def test_frontend_course_discovery_retries_transient_page_content_navigation() -> None:
    context = _Context()
    sentinel = context.new_page()
    navigator = PlaywrightRestrictedNavigator(
        cast(Any, context),
        _ORIGIN,
        canonical_wwwroot=_ORIGIN + "/current",
        transition_sentinel=cast(Any, sentinel),
    )

    document = navigator.open_frontend_course_index()

    assert document.route == "COURSE_INDEX"
    assert document.final_path == "/current/my/courses.php"
    assert document.course_ids == ("42",)
    assert document.course_index_complete is True
    assert document.html == ""
    assert len(context.pages) == 1
    assert context.pages[0] is sentinel
