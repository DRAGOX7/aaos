from __future__ import annotations

from typing import Any, cast


class _CourseRequest:
    method = "POST"
    resource_type = "xhr"
    url = (
        "https://moodle.example.edu/current/lib/ajax/service.php"
        "?info=core_course_get_enrolled_courses_by_timeline_classification"
    )
    post_data = (
        '[{"index":0,'
        '"methodname":"core_course_get_enrolled_courses_by_timeline_classification",'
        '"args":{"offset":0,"limit":0}}]'
    )


class _CourseResponse:
    status = 200
    request = _CourseRequest()

    def json(self) -> object:
        return [
            {
                "error": False,
                "data": {
                    "courses": [{"id": 42, "fullname": "Observed Course 42"}],
                    "nextoffset": 1,
                },
            }
        ]


class _Page:
    def __init__(self, context: "_Context") -> None:
        self._context = context
        self.url = "about:blank"
        self._handlers: dict[str, Any] = {}

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object:
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.url = url
        self._context.visited.append(url)
        if url == "https://moodle.example.edu/current/my/courses.php":
            response_handler = self._handlers.get("response")
            if response_handler is not None:
                response_handler(_CourseResponse())
        return None

    def content(self) -> str:
        return "<html><body>fixture</body></html>"

    def wait_for_timeout(self, milliseconds: int) -> None:
        del milliseconds

    def close(self) -> None:
        if self in self._context.pages:
            self._context.pages.remove(self)
        if not self._context.pages:
            self._context.zero_page_seen = True

    def on(self, event: str, handler: object) -> None:
        self._handlers[event] = cast(Any, handler)


class _Context:
    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self.visited: list[str] = []
        self.zero_page_seen = False
        self.request_handler: object | None = None
        self.closed = False

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        self.request_handler = handler

    def new_page(self) -> _Page:
        # Model the real persistent-Edge failure we observed: after the
        # context reaches zero pages, creating a new target is not reliable.
        if self.zero_page_seen and not self.pages:
            raise OSError("persistent context reached zero pages")
        page = _Page(self)
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True
        self.pages.clear()


def test_entity_navigation_keeps_one_inert_page_alive_between_reads() -> None:
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    context = _Context()
    sentinel = context.new_page()
    navigator = PlaywrightRestrictedNavigator(
        cast(Any, context),
        "https://moodle.example.edu",
        canonical_wwwroot="https://moodle.example.edu/current",
        transition_sentinel=cast(Any, sentinel),
    )

    # RED until the concrete navigator implements the approved fixed entity routes.
    assert hasattr(navigator, "open_course_detail")
    assert hasattr(navigator, "open_announcement_list")

    course = getattr(navigator, "open_course_detail")(42)
    assert course.route == "COURSE_DETAIL"
    assert course.final_path == "/current/course/view.php"
    assert context.zero_page_seen is False
    assert len(context.pages) == 1
    assert context.pages[0].url == "about:blank"

    announcements = getattr(navigator, "open_announcement_list")(700)
    assert announcements.route == "ANNOUNCEMENT_LIST"
    assert announcements.final_path == "/current/mod/forum/view.php"
    assert context.zero_page_seen is False
    assert len(context.pages) == 1
    assert context.pages[0].url == "about:blank"

    assert context.visited == [
        "https://moodle.example.edu/current/course/view.php?id=42",
        "https://moodle.example.edu/current/mod/forum/view.php?id=700",
    ]

    navigator.close()
    assert context.closed is True


def test_course_discovery_preserves_inert_sentinel_for_entity_reads() -> None:
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    context = _Context()
    sentinel = context.new_page()
    navigator = PlaywrightRestrictedNavigator(
        cast(Any, context),
        "https://moodle.example.edu",
        canonical_wwwroot="https://moodle.example.edu/current",
        transition_sentinel=cast(Any, sentinel),
    )

    document = navigator.open_frontend_course_index()
    assert document.route == "COURSE_INDEX"
    assert document.final_path == "/current/my/courses.php"

    # The course-discovery page is temporary. The original inert sentinel must
    # survive so the very next fixed entity read cannot encounter zero pages.
    assert context.zero_page_seen is False
    assert len(context.pages) == 1
    assert context.pages[0] is sentinel
    assert sentinel.url == "about:blank"

    detail = navigator.open_course_detail(42)
    assert detail.route == "COURSE_DETAIL"
    assert context.zero_page_seen is False
    assert len(context.pages) == 1
    assert context.pages[0] is sentinel
