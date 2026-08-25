from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import pytest

from aaos.moodle.browser_models import BrowserPolicyError

_ORIGIN = "https://moodle.example.edu"
_COURSE_INDEX = _ORIGIN + "/current/my/courses.php"
_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"


@dataclass(frozen=True)
class _Case:
    name: str
    courses: object
    nextoffset: object
    error: object = False
    status: int = 200
    offset: object = 0
    limit: object = 0
    method_name: str = _METHOD


_CASES = (
    _Case(
        name="duplicate_ids",
        courses=[{"id": 42}, {"id": 42}],
        nextoffset=2,
    ),
    _Case(
        name="zero_id",
        courses=[{"id": 0}],
        nextoffset=1,
    ),
    _Case(
        name="negative_id",
        courses=[{"id": -1}],
        nextoffset=1,
    ),
    _Case(
        name="leading_zero_string_id",
        courses=[{"id": "01"}],
        nextoffset=1,
    ),
    _Case(
        name="error_true",
        courses=[{"id": 42}],
        nextoffset=1,
        error=True,
    ),
    _Case(
        name="nextoffset_mismatch",
        courses=[{"id": 42}],
        nextoffset=2,
    ),
    _Case(
        name="nonzero_offset",
        courses=[{"id": 42}],
        nextoffset=2,
        offset=1,
    ),
    _Case(
        name="nonzero_limit",
        courses=[{"id": 42}],
        nextoffset=1,
        limit=1,
    ),
    _Case(
        name="courses_not_list",
        courses={"id": 42},
        nextoffset=1,
    ),
    _Case(
        name="course_item_not_object",
        courses=[42],
        nextoffset=1,
    ),
    _Case(
        name="missing_course_id",
        courses=[{}],
        nextoffset=1,
    ),
    _Case(
        name="wrong_http_status",
        courses=[{"id": 42}],
        nextoffset=1,
        status=500,
    ),
    _Case(
        name="unrelated_ajax_method",
        courses=[{"id": 42}],
        nextoffset=1,
        method_name="core_message_get_unsent_message",
    ),
)


class _Route:
    def __init__(self) -> None:
        self.allowed = False

    def abort(self) -> None:
        return None

    def continue_(self) -> None:
        self.allowed = True


class _Request:
    method = "POST"
    resource_type = "xhr"

    def __init__(self, case: _Case) -> None:
        self.url = (
            _ORIGIN
            + "/current/lib/ajax/service.php"
            + "?sesskey=abcdefghij&info="
            + case.method_name
        )
        self.post_data = json.dumps(
            [
                {
                    "index": 0,
                    "methodname": case.method_name,
                    "args": {
                        "classification": "all",
                        "limit": case.limit,
                        "offset": case.offset,
                        "sort": "fullname",
                        "customfieldname": "",
                        "customfieldvalue": "",
                        "requiredfields": "id",
                    },
                }
            ]
        )


class _Response:
    def __init__(self, request: _Request, case: _Case) -> None:
        self.request = request
        self.status = case.status
        self._case = case

    def json(self) -> object:
        return [
            {
                "error": self._case.error,
                "data": {
                    "courses": self._case.courses,
                    "nextoffset": self._case.nextoffset,
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
        assert url == _COURSE_INDEX
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.url = url

        request = _Request(self._context.case)
        route = _Route()
        self._context.request_handler(route, request)

        if route.allowed:
            response_handler = self._handlers.get("response")
            if response_handler is not None:
                response_handler(_Response(request, self._context.case))

    def content(self) -> str:
        return '<body class="page-mycourses"></body>'

    def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds > 0

    def close(self) -> None:
        if self in self._context.pages:
            self._context.pages.remove(self)


class _Context:
    def __init__(self, case: _Case) -> None:
        self.case = case
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


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_course_response_fail_closed_matrix(
    monkeypatch: pytest.MonkeyPatch,
    case: _Case,
) -> None:
    import aaos.moodle.browser_navigation as navigation

    ticks = iter(range(1000))
    monkeypatch.setattr(navigation, "monotonic", lambda: next(ticks))

    context = _Context(case)
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
