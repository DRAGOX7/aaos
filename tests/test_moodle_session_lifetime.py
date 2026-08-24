from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError

COURSE_INDEX = "https://moodle.example.edu/current/my/courses.php"
AUTHENTICATED_MY_COURSES_PAGE = (
    '<body id="page-my-index" class="page-mycourses pagelayout-mycourses"></body>'
)


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
            (COURSE_INDEX, AUTHENTICATED_MY_COURSES_PAGE),
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
            (COURSE_INDEX, AUTHENTICATED_MY_COURSES_PAGE),
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


def test_authenticated_my_courses_page_does_not_require_course_or_pagination_content() -> None:
    from aaos.moodle.browser_parsing import has_authenticated_my_courses_page

    assert has_authenticated_my_courses_page(AUTHENTICATED_MY_COURSES_PAGE) is True
    assert (
        has_authenticated_my_courses_page("<form action='/current/login/index.php'></form>")
        is False
    )


def test_exact_course_index_with_login_document_is_not_authenticated() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    elapsed = [0.0]
    page = _FakePage(
        [(COURSE_INDEX, "<body class='page-login-index'></body>")],
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


def test_zero_course_authenticated_my_courses_page_is_authenticated() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    elapsed = [0.0]
    page = _FakePage(
        [(COURSE_INDEX, AUTHENTICATED_MY_COURSES_PAGE)],
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


def test_closed_target_during_authentication_proof_returns_not_authenticated() -> None:
    from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

    class _ClosedTargetPage:
        url = COURSE_INDEX

        def content(self) -> str:
            raise PlaywrightError("redacted")

        def wait_for_timeout(self, _milliseconds: int) -> None:
            raise AssertionError("closed target must not wait again")

    assert (
        wait_for_authenticated_course_index(
            _ClosedTargetPage(),
            course_index_url=COURSE_INDEX,
            timeout_seconds=3,
        )
        is False
    )
