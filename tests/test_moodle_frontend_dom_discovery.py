from __future__ import annotations

import json

import pytest

_ORIGIN = "https://moodle.example.edu"
_WWWROOT = _ORIGIN + "/current"
_METHOD = "core_course_get_enrolled_courses_by_timeline_classification"


def _payload(*methods: str) -> str:
    return json.dumps(
        [
            {"index": index, "methodname": method, "args": {"ignored": "fixture"}}
            for index, method in enumerate(methods)
        ]
    )


def test_policy_allows_only_the_proven_single_frontend_course_ajax_operation() -> None:
    from aaos.moodle.browser_navigation import is_approved_frontend_course_ajax_request

    assert is_approved_frontend_course_ajax_request(
        "POST",
        _WWWROOT + "/lib/ajax/service.php?fixture=redacted",
        "xhr",
        _payload(_METHOD),
        base_origin=_ORIGIN,
        canonical_wwwroot=_WWWROOT,
    )


@pytest.mark.parametrize(
    ("method", "url", "resource_type", "body"),
    (
        ("POST", _WWWROOT + "/lib/ajax/service.php", "xhr", _payload("core_user_update")),
        ("POST", _WWWROOT + "/lib/ajax/service.php", "xhr", _payload(_METHOD, _METHOD)),
        ("GET", _WWWROOT + "/lib/ajax/service.php", "xhr", _payload(_METHOD)),
        ("POST", _WWWROOT + "/lib/ajax/other.php", "xhr", _payload(_METHOD)),
        ("POST", "https://other.example/lib/ajax/service.php", "xhr", _payload(_METHOD)),
        ("POST", _WWWROOT + "/lib/ajax/service.php", "document", _payload(_METHOD)),
    ),
)
def test_policy_rejects_every_non_proven_frontend_ajax_request(
    method: str, url: str, resource_type: str, body: str
) -> None:
    from aaos.moodle.browser_navigation import is_approved_frontend_course_ajax_request

    assert not is_approved_frontend_course_ajax_request(
        method,
        url,
        resource_type,
        body,
        base_origin=_ORIGIN,
        canonical_wwwroot=_WWWROOT,
    )


def test_policy_rejects_oversized_frontend_ajax_body_before_decoding() -> None:
    from aaos.moodle.browser_navigation import is_approved_frontend_course_ajax_request

    oversized = json.dumps([{"index": 0, "methodname": _METHOD, "args": {"padding": "x" * 16_384}}])

    assert not is_approved_frontend_course_ajax_request(
        "POST",
        _WWWROOT + "/lib/ajax/service.php",
        "xhr",
        oversized,
        base_origin=_ORIGIN,
        canonical_wwwroot=_WWWROOT,
    )


def test_parser_accepts_the_live_proven_canonical_dom_course_link_without_fixture_attribute() -> (
    None
):
    from aaos.moodle.browser_parsing import parse_frontend_course_index

    result = parse_frontend_course_index(
        '<a href="/current/course/view.php?id=42">redacted</a>',
        expected_origin=_ORIGIN,
    )

    assert tuple(course.source_id for course in result) == ("42",)
    assert all(course.title == "" for course in result)


def test_frontend_settles_an_authenticated_explicit_empty_course_state() -> None:
    from aaos.moodle.browser_navigation import _is_frontend_course_index_settled

    assert _is_frontend_course_index_settled(
        "<body class='page-mycourses'><div data-region='no-courses'></div></body>",
        _ORIGIN,
    )


@pytest.mark.parametrize(
    "markup",
    (
        '<body class="page-mycourses"><script>"data-region=\\"no-courses\\""</script></body>',
        '<body class="page-mycourses">data-region="no-courses"</body>',
    ),
)
def test_frontend_empty_state_does_not_accept_text_or_script_content(markup: str) -> None:
    from aaos.moodle.browser_navigation import _is_frontend_course_index_settled

    assert not _is_frontend_course_index_settled(markup, _ORIGIN)


@pytest.mark.parametrize(
    "markup",
    (
        '<a href="/current/course/view.php?id=42">redacted</a>'
        '<a href="/current/course/view.php?id=42">redacted</a>',
        '<a href="/current/course/view.php?id=zero">redacted</a>',
        '<a href="/current/course/view.php?id=42&other=redacted">redacted</a>',
    ),
)
def test_frontend_dom_parser_rejects_duplicate_or_invalid_course_identity(markup: str) -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_course_index

    with pytest.raises(BrowserPolicyError):
        parse_frontend_course_index(markup, expected_origin=_ORIGIN)


@pytest.mark.parametrize("course_id", ("\u00b2", "9" * 11))
def test_frontend_dom_parser_rejects_non_ascii_or_unbounded_course_identity(
    course_id: str,
) -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_course_index

    with pytest.raises(BrowserPolicyError):
        parse_frontend_course_index(
            f'<a href="/current/course/view.php?id={course_id}">redacted</a>',
            expected_origin=_ORIGIN,
        )


def test_production_frontend_policy_does_not_reference_session_key_or_construct_fetch() -> None:
    from pathlib import Path

    source = (
        Path(__file__).parents[1] / "src" / "aaos" / "moodle" / "browser_navigation.py"
    ).read_text(encoding="utf-8")

    assert "sesskey" not in source.lower()
    assert "fetch(" not in source


def test_restricted_navigator_has_no_route_parameterized_public_navigation() -> None:
    from pathlib import Path

    source = (
        Path(__file__).parents[1] / "src" / "aaos" / "moodle" / "browser_navigation.py"
    ).read_text(encoding="utf-8")

    assert "def open(self, route:" not in source
