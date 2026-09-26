from __future__ import annotations

from typing import Any

from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator
from aaos.moodle.embedded_drive import DriveFolderReference

_DRIVE_TARGET = "https://drive.google.com/embeddedfolderview?id=folder_ALPHA_123"


class _Request:
    def __init__(
        self,
        url: str,
        *,
        method: str = "GET",
        resource_type: str = "document",
    ) -> None:
        self.method = method
        self.url = url
        self.resource_type = resource_type
        self.post_data = None


class _Route:
    def __init__(self) -> None:
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self.continued = True

    def abort(self) -> None:
        self.aborted = True


class _Sentinel:
    url = "about:blank"

    def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
        del wait_until, timeout

    def content(self) -> str:
        return ""

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def on(self, _event: str, _handler: object) -> None:
        return None

    def route(self, _pattern: str, _handler: object) -> None:
        return None

    def close(self) -> None:
        raise AssertionError("sentinel must survive provider read")


class _Page:
    def __init__(self, context: "_Context") -> None:
        self._context = context
        self.url = _DRIVE_TARGET
        self.page_route_handler: Any | None = None
        self.exact_route = _Route()
        self.unrelated_route = _Route()
        self.closed = False

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        self.page_route_handler = handler

    def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: int,
    ) -> None:
        assert url == _DRIVE_TARGET
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        assert self.page_route_handler is not None

        # The explicit folder document is allowed by the page-scoped
        # capability.
        assert callable(self.page_route_handler)
        self.page_route_handler(self.exact_route, _Request(url))

        # An unrelated request from the Drive document remains blocked.
        self.page_route_handler(
            self.unrelated_route,
            _Request(
                "https://drive.google.com/images/branding/product/drive.svg",
                resource_type="image",
            ),
        )

        self.url = url

    def content(self) -> str:
        return """
        <a href="https://drive.google.com/file/d/file_ALPHA_123/view">
          <div class="flip-entry-title">Lecture.pdf</div>
        </a>
        """

    def wait_for_timeout(self, milliseconds: int) -> None:
        del milliseconds

    def on(self, event: str, handler: object) -> None:
        del event, handler

    def close(self) -> None:
        self.closed = True
        self._context.pages.remove(self)


class _Context:
    def __init__(self) -> None:
        self.sentinel = _Sentinel()
        self.pages: list[Any] = [self.sentinel]
        self.context_handler: Any | None = None
        self.page: _Page | None = None

    def route(self, pattern: str, handler: object) -> None:
        assert pattern == "**/*"
        self.context_handler = handler

    def new_page(self) -> _Page:
        self.page = _Page(self)
        self.pages.append(self.page)
        return self.page

    def close(self) -> None:
        return None


def test_drive_reader_is_explicit_and_page_scoped() -> None:
    context = _Context()

    navigator = PlaywrightRestrictedNavigator(
        context,
        "https://moodle.example.edu",
        canonical_wwwroot="https://moodle.example.edu/current",
        transition_sentinel=context.sentinel,
    )

    # The existing context-wide policy must still reject Drive.
    assert context.context_handler is not None

    global_route = _Route()

    context.context_handler(
        global_route,
        _Request(_DRIVE_TARGET),
    )

    assert global_route.aborted is True
    assert global_route.continued is False

    html = navigator.read_embedded_drive_folder(DriveFolderReference("folder_ALPHA_123"))

    assert "Lecture.pdf" in html

    assert context.page is not None
    assert context.page.exact_route.continued is True
    assert context.page.exact_route.aborted is False

    assert context.page.unrelated_route.aborted is True
    assert context.page.unrelated_route.continued is False

    assert context.page.closed is True
    assert context.pages == [context.sentinel]


def test_drive_reader_does_not_change_normal_cross_origin_policy() -> None:
    context = _Context()

    navigator = PlaywrightRestrictedNavigator(
        context,
        "https://moodle.example.edu",
        canonical_wwwroot="https://moodle.example.edu/current",
        transition_sentinel=context.sentinel,
    )

    navigator.read_embedded_drive_folder(DriveFolderReference("folder_ALPHA_123"))

    assert context.context_handler is not None

    route = _Route()

    context.context_handler(
        route,
        _Request("https://drive.google.com/file/d/file_ALPHA_123/view"),
    )

    assert route.aborted is True
    assert route.continued is False
