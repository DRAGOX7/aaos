from __future__ import annotations

import json
from typing import Any, cast

import pytest

_ORIGIN = "https://moodle.example.edu"
_COURSE_INDEX = _ORIGIN + "/current/my/courses.php"
_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"


class _Route:
    def __init__(self) -> None:
        self.allowed = False

    def abort(self) -> None:
        return None

    def continue_(self) -> None:
        self.allowed = True


class _Request:
    method = "POST"
    url = (
        _ORIGIN
        + "/current/lib/ajax/service.php"
        + "?sesskey=abcdefghij&info="
        + _METHOD
    )
    resource_type = "xhr"

    def __init__(self) -> None:
        self.post_data = json.dumps(
            [
                {
                    "index": 0,
                    "methodname": _METHOD,
                    "args": {
                        "classification": "all",
                        "limit": 0,
                        "offset": 0,
                        "sort": "fullname",
                        "customfieldname": "",
                        "customfieldvalue": "",
                        "requiredfields": "id",
                    },
                }
            ]
        )


class _Response:
    status = 200

    def __init__(self, request: _Request) -> None:
        self.request = request

    def json(self) -> object:
        return [
            {
                "error": False,
                "data": {
                    "courses": [
                        {"id": 42},
                        {"id": 43},
                    ],
                    "nextoffset": 2,
                },
            }
        ]


class _Page:
    def __init__(self, context: "_Context", *, sentinel: bool) -> None:
        self._context = context
        self._sentinel = sentinel
        self.url = "about:blank" if sentinel else _COURSE_INDEX
        self._handlers: dict[str, Any] = {}

    def on(self, event: str, handler: object) -> None:
        self._handlers[event] = cast(Any, handler)

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        assert url == _COURSE_INDEX
        self.url = url

        request = _Request()
        route = _Route()
        self._context.request_handler(route, request)
        assert route.allowed is True

        response_handler = self._handlers.get("response")
        if response_handler is not None:
            response_handler(_Response(request))

    def content(self) -> str:
        # Deliberately never render course links. The new production path must
        # use Moodle's own structured response instead of waiting for cards.
        return '<body class="page-mycourses"></body>'

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


def test_frontend_course_discovery_uses_complete_moodle_owned_response_without_dom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.moodle.browser_navigation as navigation

    # Make the old DOM-settle loop expire immediately rather than waiting 15 real
    # seconds. The desired response-driven implementation returns before this matters.
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

    document = navigator.open_frontend_course_index()

    assert document.route == "COURSE_INDEX"
    assert document.final_origin == _ORIGIN
    assert document.final_path == "/current/my/courses.php"

    # RED: these response-derived fields do not exist yet.
    assert document.course_ids == ("42", "43")
    assert document.course_index_complete is True

    # The inert sentinel survives for the next entity-read phase.
    assert len(context.pages) == 1
    assert context.pages[0] is sentinel
    assert sentinel.url == "about:blank"
