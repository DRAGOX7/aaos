from __future__ import annotations

from collections.abc import Callable

COURSE_INDEX = "https://moodle.example.edu/current/my/courses.php"
COURSE_INDEX_MARKER = """
<a data-moodle-course=\"true\" href=\"https://moodle.example.edu/current/course/view.php?id=42\">Synthetic</a>
<nav data-pagination-pages=\"0\" data-pagination-exhausted=\"true\"></nav>
"""


class _FakePage:
    def __init__(self, states: list[tuple[str, str]], advance_clock: Callable[[], None]) -> None:
        self._states = iter(states)
        self._advance_clock = advance_clock
        self.url, self._content = next(self._states)
        self.content_calls = 0
        self.wait_calls = 0

    def content(self) -> str:
        self.content_calls += 1
        return self._content

    def wait_for_timeout(self, milliseconds: int) -> None:
        assert milliseconds == 1000
        self.wait_calls += 1
        self._advance_clock()
        try:
            self.url, self._content = next(self._states)
        except StopIteration:
            pass


def test_session_lifetime_report_has_only_fixed_process_bound_outcome() -> None:
    from aaos.moodle.session_lifetime import SessionLifetimeReport, render_session_lifetime_report

    rendered = render_session_lifetime_report(SessionLifetimeReport("AUTHENTICATED", "LOGIN_ROUTE"))

    assert rendered.splitlines() == [
        "SESSION_LIFETIME_DIAGNOSTIC",
        "SAME_CONTEXT_SESSION=AUTHENTICATED",
        "RELAUNCHED_SESSION=LOGIN_ROUTE",
        "PROFILE_BINDING_MATCH=PASS",
        "CLEAN_RELAUNCH=PASS",
        "SESSION_LIFETIME_RESULT=PROCESS_BOUND_SESSION_CONFIRMED",
    ]


def test_requested_course_index_before_authenticated_document_keeps_interaction_active() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    elapsed = [0.0]
    page = _FakePage(
        [
            (COURSE_INDEX, "<form action='/current/login/index.php'></form>"),
            ("https://moodle.example.edu/current/login/index.php", "<form></form>"),
            (COURSE_INDEX, COURSE_INDEX_MARKER),
        ],
        lambda: elapsed.__setitem__(0, elapsed[0] + 1.0),
    )

    assert (
        wait_for_authenticated_course_index(
            page,
            course_index_url=COURSE_INDEX,
            timeout_seconds=3,
            monotonic=lambda: elapsed[0],
            poll_milliseconds=1000,
        )
        is True
    )
    assert page.wait_calls == 3


def test_login_or_sso_without_course_marker_times_out_without_authentication() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    elapsed = [0.0]
    page = _FakePage(
        [
            ("https://moodle.example.edu/current/login/index.php", "<form></form>"),
            ("https://login.microsoftonline.com/tenant", "<form></form>"),
        ],
        lambda: elapsed.__setitem__(0, elapsed[0] + 1.0),
    )

    assert (
        wait_for_authenticated_course_index(
            page,
            course_index_url=COURSE_INDEX,
            timeout_seconds=3,
            monotonic=lambda: elapsed[0],
            poll_milliseconds=1000,
        )
        is False
    )
    assert page.content_calls == 0
    assert page.wait_calls == 3


def test_transient_course_index_marker_followed_by_login_never_authenticates() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    elapsed = [0.0]
    page = _FakePage(
        [
            (COURSE_INDEX, COURSE_INDEX_MARKER),
            ("https://moodle.example.edu/current/login/index.php", "<form></form>"),
        ],
        lambda: elapsed.__setitem__(0, elapsed[0] + 1.0),
    )

    assert (
        wait_for_authenticated_course_index(
            page,
            course_index_url=COURSE_INDEX,
            timeout_seconds=3,
            monotonic=lambda: elapsed[0],
            poll_milliseconds=1000,
        )
        is False
    )


def test_course_index_marker_requires_course_and_pagination_structure() -> None:
    from aaos.moodle.browser_parsing import has_course_index_marker

    assert has_course_index_marker(COURSE_INDEX_MARKER) is True
    assert has_course_index_marker("<form action='/current/login/index.php'></form>") is False
