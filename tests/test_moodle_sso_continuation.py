"""Phase 10 addendum: continue a remembered Microsoft sign-in, never touching a field."""

from __future__ import annotations

import pytest

from aaos.moodle.sso_continuation import (
    SignInResult,
    continue_remembered_sign_in,
    find_sso_href,
)

ORIGIN = "https://elearn.just.edu.jo"
LOGIN = ORIGIN + "/current/login/index.php"
COURSES = ORIGIN + "/current/my/courses.php"
SSO = ORIGIN + "/current/auth/oauth2/login.php?id=1&wantsurl=%2Fcurrent%2F&sesskey=abc"


def login_html(*hrefs: str) -> str:
    links = "".join(f'<a class="btn" href="{href}">Log in with Microsoft</a>' for href in hrefs)
    return f"<html><body><h1>Welcome back</h1>{links}</body></html>"


def test_the_one_same_origin_sso_link_is_found() -> None:
    html = login_html(SSO.replace("&", "&amp;"))

    assert find_sso_href(html, LOGIN, ORIGIN) == SSO


@pytest.mark.parametrize(
    "hrefs",
    [
        (),
        (SSO, ORIGIN + "/current/auth/oauth2/login.php?id=2&sesskey=abc"),
        ("https://evil.example/current/auth/oauth2/login.php?id=1",),
        (ORIGIN + "/current/auth/oauth2/login.php.evil?id=1",),
        (ORIGIN + "/current/login/forgot_password.php",),
        ("javascript:alert(1)",),
    ],
)
def test_anything_but_exactly_one_same_origin_sso_link_is_ignored(hrefs: tuple[str, ...]) -> None:
    assert find_sso_href(login_html(*hrefs), LOGIN, ORIGIN) is None


def test_a_relative_sso_link_resolves_against_the_moodle_page() -> None:
    html = login_html("/current/auth/oauth2/login.php?id=1&amp;sesskey=abc")

    assert (
        find_sso_href(html, LOGIN, ORIGIN)
        == ORIGIN + "/current/auth/oauth2/login.php?id=1&sesskey=abc"
    )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakePage:
    """Records navigation; any input action fails the test."""

    def __init__(self, html: str, after_sso: list[str], clock: Clock) -> None:
        self.url = LOGIN
        self._html = html
        self._after_sso = after_sso
        self._clock = clock
        self.visited: list[str] = []

    def content(self) -> str:
        return self._html

    def goto(self, url: str, **_kwargs: object) -> None:
        self.visited.append(url)
        if url == SSO:
            self.url = self._after_sso.pop(0) if self._after_sso else self.url
        else:
            self.url = url

    def wait_for_timeout(self, milliseconds: int) -> None:
        self._clock.now += milliseconds / 1000
        if self._after_sso:
            self.url = self._after_sso.pop(0)

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"sign-in continuation must not use page.{name}")


def test_a_remembered_sign_in_continues_to_the_course_list() -> None:
    clock = Clock()
    page = FakePage(
        login_html(SSO),
        ["https://login.microsoftonline.com/common/oauth2/authorize", ORIGIN + "/current/my/"],
        clock,
    )

    result = continue_remembered_sign_in(
        page, base_origin=ORIGIN, course_index_url=COURSES, timeout_seconds=90, monotonic=clock
    )

    assert result is SignInResult.CONTINUED
    assert page.visited == [SSO, COURSES]


def test_a_microsoft_prompt_is_never_touched_and_the_run_ends_at_its_budget() -> None:
    clock = Clock()
    password_page = "https://login.microsoftonline.com/common/login"
    page = FakePage(login_html(SSO), [password_page] * 10_000, clock)

    result = continue_remembered_sign_in(
        page, base_origin=ORIGIN, course_index_url=COURSES, timeout_seconds=90, monotonic=clock
    )

    assert result is SignInResult.TIMED_OUT
    assert page.visited == [SSO]  # one follow, nothing else
    assert 90 <= clock.now < 92


def test_without_a_login_page_nothing_is_attempted() -> None:
    clock = Clock()
    page = FakePage(login_html(SSO), [], clock)
    page.url = COURSES

    result = continue_remembered_sign_in(
        page, base_origin=ORIGIN, course_index_url=COURSES, timeout_seconds=90, monotonic=clock
    )

    assert result is SignInResult.NOT_ATTEMPTED
    assert page.visited == []


def test_without_exactly_one_sso_link_nothing_is_attempted() -> None:
    clock = Clock()
    page = FakePage(login_html(), [], clock)

    result = continue_remembered_sign_in(
        page, base_origin=ORIGIN, course_index_url=COURSES, timeout_seconds=90, monotonic=clock
    )

    assert result is SignInResult.NOT_ATTEMPTED
    assert page.visited == []
