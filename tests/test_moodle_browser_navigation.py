from __future__ import annotations

import pytest


def test_navigator_installs_interception_before_creating_page_and_blocks_unsafe_requests() -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    calls: list[str] = []

    class Request:
        method = "POST"
        url = "https://moodle.example.edu/current/lib/ajax/service.php"

    class Route:
        def abort(self) -> None:
            calls.append("abort")

        def continue_(self) -> None:
            calls.append("continue")

    class Page:
        url = "https://moodle.example.edu/current/my/courses.php"

        def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
            calls.append("goto")

        def content(self) -> str:
            return "<nav data-pagination-pages='0' data-pagination-exhausted='true'></nav>"

        def close(self) -> None:
            calls.append("page-close")

        def on(self, event: str, _handler: object) -> None:
            calls.append(f"page-{event}")

    class Context:
        def route(self, _pattern: str, handler: object) -> None:
            calls.append("route")
            handler(Route(), Request())  # type: ignore[operator]

        def new_page(self) -> Page:
            calls.append("new-page")
            return Page()

        def close(self) -> None:
            calls.append("context-close")

    navigator = PlaywrightRestrictedNavigator(Context(), "https://moodle.example.edu")
    document = navigator.open("COURSE_INDEX")
    navigator.close()

    assert document.route == "COURSE_INDEX"
    assert calls.index("route") < calls.index("new-page")
    assert "page-popup" in calls
    assert "abort" in calls
    assert calls[-1] == "context-close"
    with pytest.raises(BrowserPolicyError):
        navigator.open("RESOURCE_METADATA")
