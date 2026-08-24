from __future__ import annotations

import pytest


def test_session_read_diagnostic_renders_only_fixed_non_secret_values() -> None:
    from aaos.moodle.session_read import SessionReadDiagnostic, render_session_read_diagnostic

    diagnostic = SessionReadDiagnostic(
        authenticated_page="PASS",
        sentinel_transition="PASS",
        collection_page="PASS",
        bridge_stage="PASS",
        ajax_permit_stage="REQUEST_ALLOWED",
        ajax_permit_rejection_reason="NOT_APPLICABLE",
        ajax_response_stage="REDUCED_SCHEMA_PASS",
        pagination_stage="EMPTY_TERMINAL_PAGE",
        root_cause="NO_FAILURE",
    )

    assert render_session_read_diagnostic(diagnostic) == "\n".join(
        (
            "SESSION_READ_DIAGNOSTIC",
            "AUTHENTICATED_PAGE=PASS",
            "SENTINEL_TRANSITION=PASS",
            "COLLECTION_PAGE=PASS",
            "BRIDGE_STAGE=PASS",
            "AJAX_PERMIT_STAGE=REQUEST_ALLOWED",
            "AJAX_PERMIT_REJECTION_REASON=NOT_APPLICABLE",
            "AJAX_RESPONSE_STAGE=REDUCED_SCHEMA_PASS",
            "PAGINATION_STAGE=EMPTY_TERMINAL_PAGE",
            "SESSION_READ_ROOT_CAUSE=NO_FAILURE",
        )
    )


def test_only_the_canonical_operation_specification_is_accepted() -> None:
    from aaos.moodle.session_read import (
        _ENROLLED_COURSES_OPERATION,
        SessionReadPolicyError,
        authorize_session_read_operation,
    )

    authorize_session_read_operation(_ENROLLED_COURSES_OPERATION)
    with pytest.raises(SessionReadPolicyError):
        authorize_session_read_operation(object())
    with pytest.raises(SessionReadPolicyError):
        authorize_session_read_operation(
            type(_ENROLLED_COURSES_OPERATION)(
                "core_course_get_enrolled_courses_by_timeline_classification", True, True
            )
        )


def test_fixed_bridge_uses_direct_fetch_without_moodle_core_ajax() -> None:
    from aaos.moodle.session_read import _FIXED_BRIDGE_SCRIPT

    assert "fetch(" in _FIXED_BRIDGE_SCRIPT
    assert "core/ajax" not in _FIXED_BRIDGE_SCRIPT
    assert "require([" not in _FIXED_BRIDGE_SCRIPT
    assert "AbortController" in _FIXED_BRIDGE_SCRIPT
    assert "15000" in _FIXED_BRIDGE_SCRIPT
    assert "course_ids" in _FIXED_BRIDGE_SCRIPT
    assert "next_offset" in _FIXED_BRIDGE_SCRIPT


def test_ajax_permit_accepts_only_one_canonical_post_request() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    nonce = "test-provenance-nonce-a"
    permit = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", nonce)
    permit.arm(0)
    request_url = (
        "https://moodle.example.edu/current/lib/ajax/service.php?"
        "sesskey=private&info=core_course_get_enrolled_courses_by_timeline_classification&"
        "nosessionupdate=true"
    )
    permit.consume("POST", request_url, {"x-aaos-request-provenance": nonce})
    with pytest.raises(SessionReadPolicyError):
        permit.consume("POST", request_url, {"x-aaos-request-provenance": nonce})


def test_ajax_permit_does_not_authorize_wrong_origin_path_or_method() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    permit = _OneShotMoodleAjaxPermit(
        "https://moodle.example.edu/current", "test-provenance-nonce-b"
    )
    permit.arm(0)
    with pytest.raises(SessionReadPolicyError):
        permit.consume(
            "GET",
            "https://moodle.example.edu/current/lib/ajax/service.php",
            {"x-aaos-request-provenance": "test-provenance-nonce-b"},
        )


def test_ajax_permit_rejects_missing_and_wrong_nonce_without_consuming() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    nonce = "test-provenance-nonce-c"
    url = "https://moodle.example.edu/current/lib/ajax/service.php"
    permit = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", nonce)
    permit.arm(0)

    with pytest.raises(SessionReadPolicyError):
        permit.consume("POST", url, {})
    with pytest.raises(SessionReadPolicyError):
        permit.consume("POST", url, {"x-aaos-request-provenance": "wrong"})

    permit.consume("POST", url, {"X-AAOS-Request-Provenance": nonce})
    assert permit.consumed is True


def test_ajax_permit_nonce_is_ephemeral_and_never_rendered() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    nonce = "test-provenance-never-render"
    permit = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", nonce)
    permit.arm(0)

    assert nonce not in repr(permit)
    with pytest.raises(SessionReadPolicyError) as failure:
        permit.consume("POST", "https://moodle.example.edu/current/lib/ajax/service.php", {})
    assert nonce not in str(failure.value)


def test_reduced_response_schema_and_pagination_are_strict_and_bounded() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, collect_course_ids

    responses = iter(
        (
            {"course_ids": [10, 11], "next_offset": 2},
            {"course_ids": [], "next_offset": 2},
        )
    )
    assert collect_course_ids(lambda _offset: next(responses)) == (10, 11)

    with pytest.raises(SessionReadPolicyError):
        collect_course_ids(lambda _offset: {"course_ids": [10, 10], "next_offset": 2})


@pytest.mark.parametrize(
    "response",
    [
        {"course_ids": [0], "next_offset": 1},
        {"course_ids": [True], "next_offset": 1},
        {"course_ids": [1], "next_offset": 0},
        {"course_ids": [1], "next_offset": 2},
        {"course_ids": [1], "next_offset": -1},
        {"course_ids": [1], "next_offset": 1, "raw": "forbidden"},
        {"courses": [1], "nextoffset": 1},
    ],
)
def test_reduced_response_rejects_malformed_or_incomplete_pages(response: object) -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, collect_course_ids

    with pytest.raises(SessionReadPolicyError):
        collect_course_ids(lambda _offset: response)


def test_permit_rejects_wrong_origin_and_path_without_inspecting_wire_format() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    bad_requests = (
        "https://other.example/current/lib/ajax/service.php",
        "https://moodle.example.edu/currentevil/lib/ajax/service.php",
    )
    for url in bad_requests:
        nonce = "test-provenance-bad-target"
        permit = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", nonce)
        permit.arm(0)
        with pytest.raises(SessionReadPolicyError):
            permit.consume("POST", url, {"x-aaos-request-provenance": nonce})


def test_consumed_permit_remains_one_shot_while_a_fresh_permit_accepts_next_offset() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, _OneShotMoodleAjaxPermit

    url = (
        "https://moodle.example.edu/current/lib/ajax/service.php?"
        "sesskey=opaque&info=core_course_get_enrolled_courses_by_timeline_classification&"
        "nosessionupdate=true"
    )

    first_nonce = "test-provenance-first"
    second_nonce = "test-provenance-second"
    first = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", first_nonce)
    first.arm(0)
    first.consume("POST", url, {"x-aaos-request-provenance": first_nonce})
    with pytest.raises(SessionReadPolicyError):
        first.consume("POST", url, {"x-aaos-request-provenance": first_nonce})

    second = _OneShotMoodleAjaxPermit("https://moodle.example.edu/current", second_nonce)
    second.arm(100)
    with pytest.raises(SessionReadPolicyError):
        second.consume("POST", url, {"x-aaos-request-provenance": first_nonce})
    second.consume("POST", url, {"x-aaos-request-provenance": second_nonce})
    with pytest.raises(SessionReadPolicyError):
        second.consume("POST", url, {"x-aaos-request-provenance": second_nonce})


def test_pagination_rejects_duplicate_across_pages_and_requires_empty_terminal_page() -> None:
    from aaos.moodle.session_read import SessionReadPolicyError, collect_course_ids

    pages = iter(
        (
            {"course_ids": [1], "next_offset": 1},
            {"course_ids": [1], "next_offset": 2},
        )
    )
    with pytest.raises(SessionReadPolicyError):
        collect_course_ids(lambda _offset: next(pages))

    with pytest.raises(SessionReadPolicyError):
        collect_course_ids(lambda offset: {"course_ids": [offset + 1], "next_offset": offset + 1})
