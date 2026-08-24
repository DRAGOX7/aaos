from __future__ import annotations

from typing import Any

import pytest
from playwright._impl._errors import TargetClosedError
from playwright.sync_api import Error as PlaywrightError


def test_navigator_installs_interception_before_creating_page_and_blocks_unsafe_requests() -> None:
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    calls: list[str] = []

    class Request:
        method = "POST"
        url = "https://moodle.example.edu/current/lib/ajax/service.php"
        resource_type = "xhr"

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

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def close(self) -> None:
            calls.append("page-close")

        def on(self, event: str, _handler: object) -> None:
            calls.append(f"page-{event}")

    class Context:
        pages: list[Any] = []

        def route(self, _pattern: str, handler: object) -> None:
            calls.append("route")
            handler(Route(), Request())  # type: ignore[operator]

        def new_page(self) -> Page:
            calls.append("new-page")
            return Page()

        def close(self) -> None:
            calls.append("context-close")

    navigator = PlaywrightRestrictedNavigator(Context(), "https://moodle.example.edu")
    document = navigator.open_course_index_document()
    navigator.close()

    assert document.route == "COURSE_INDEX"
    assert calls.index("route") < calls.index("new-page")
    assert "page-popup" in calls
    assert "abort" in calls
    assert navigator.failure_stage == "NONE"
    assert calls[-1] == "context-close"


def test_navigator_keeps_document_when_owned_page_is_already_closed_during_cleanup() -> None:
    """A completed read must survive only the benign closed-page cleanup case."""
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    class Context:
        def __init__(self) -> None:
            self.pages: list[Any] = []

        def route(self, _pattern: str, _handler: object) -> None:
            return None

        def new_page(self) -> Page:
            page = Page(self)
            self.pages.append(page)
            return page

        def close(self) -> None:
            return None

    class Page:
        url = "https://moodle.example.edu/current/my/courses.php"

        def __init__(self, context: Context) -> None:
            self._context = context

        def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
            return None

        def content(self) -> str:
            return "<nav data-pagination-pages='0' data-pagination-exhausted='true'></nav>"

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def close(self) -> None:
            self._context.pages.remove(self)
            raise TargetClosedError("redacted")

        def on(self, _event: str, _handler: object) -> None:
            return None

    navigator = PlaywrightRestrictedNavigator(Context(), "https://moodle.example.edu")

    document = navigator.open_course_index_document()

    assert document.route == "COURSE_INDEX"


def test_redirect_classifier_returns_only_fixed_structural_categories() -> None:
    from aaos.moodle.browser_navigation import classify_redirect

    origin = "https://moodle.example.edu"
    assert classify_redirect(origin + "/current/login/index.php", origin) == "LOGIN_ROUTE"
    assert classify_redirect(origin + "/current/my/index.php", origin) == "MOODLE_MY_ROUTE"
    assert (
        classify_redirect(origin + "/current/my/courses.php?redirect=1", origin)
        == "COURSE_INDEX_VARIANT"
    )
    assert classify_redirect("https://login.microsoftonline.com/login", origin) == "SSO_EXTERNAL"


def test_navigator_classifies_playwright_collection_page_creation_failure() -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator

    class Context:
        pages: list[Any] = []

        def route(self, _pattern: str, _handler: object) -> None:
            return None

        def new_page(self) -> Any:
            raise PlaywrightError("redacted")

        def close(self) -> None:
            return None

    navigator = PlaywrightRestrictedNavigator(Context(), "https://moodle.example.edu")

    with pytest.raises(BrowserPolicyError):
        navigator.open_course_index_document()

    assert navigator.failure_stage == "COLLECTION_PAGE_CREATE"
