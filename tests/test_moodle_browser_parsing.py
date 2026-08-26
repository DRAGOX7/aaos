from __future__ import annotations

import pytest

COURSES = """
<a data-moodle-course="true" href="https://moodle.example.edu/current/course/view.php?id=42">Synthetic</a>
<a data-moodle-course="true" href="https://moodle.example.edu/current/course/view.php?id=43">Synthetic two</a>
<nav data-pagination-pages="0,1" data-pagination-exhausted="true"></nav>
"""


def test_course_parser_uses_numeric_url_identity_and_complete_proof() -> None:
    from aaos.moodle.browser_parsing import parse_course_index

    result = parse_course_index(COURSES, expected_origin="https://moodle.example.edu")

    assert tuple(course.source_id for course in result.records) == ("42", "43")
    assert result.proof.exhausted is True


def test_course_parser_rejects_title_only_or_incomplete_markup() -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_course_index

    with pytest.raises(BrowserPolicyError):
        parse_course_index(
            "<a data-moodle-course='true'>Synthetic</a>",
            expected_origin="https://moodle.example.edu",
        )


def test_course_index_structure_diagnostic_reports_only_fixed_non_content_facts() -> None:
    from aaos.moodle.browser_parsing import diagnose_course_index_structure

    diagnostic = diagnose_course_index_structure(
        COURSES, expected_origin="https://moodle.example.edu"
    )

    assert diagnostic.render().splitlines() == [
        "COURSE_INDEX_STRUCTURE_DIAGNOSTIC",
        "PAGE_IDENTITY=OTHER",
        "COURSE_CONTAINER_COUNT=2",
        "CANDIDATE_COURSE_LINK_COUNT=2",
        "NUMERIC_COURSE_ID_LINK_COUNT=2",
        "COURSE_DATA_ATTRIBUTE_COUNT=0",
        "PAGINATION_CONTAINER_COUNT=1",
        "NEXT_PAGE_LINK_COUNT=0",
        "EMPTY_STATE_MARKER=NO",
        "KNOWN_FIXTURE_SCHEMA_MATCH=YES",
        "COURSE_LINK_SHAPE=COURSE_VIEW_ID_QUERY",
    ]
    assert "Synthetic" not in diagnostic.render()
    assert "42" not in diagnostic.render()


def test_assignment_announcement_and_resource_ids_are_typed() -> None:
    from aaos.moodle.browser_parsing import (
        parse_announcement_list,
        parse_assignment_list,
        parse_frontend_course_entities,
        parse_resource_metadata,
    )

    assignments = parse_assignment_list(
        '<a data-moodle-assignment="true" href="https://moodle.example.edu/current/mod/assign/view.php?id=7">A</a><nav data-pagination-pages="0" data-pagination-exhausted="true"></nav>',
        course_source_id="42",
        expected_origin="https://moodle.example.edu",
    )
    announcements = parse_announcement_list(
        '<section data-announcement-forum="true"><a href="https://moodle.example.edu/current/mod/forum/discuss.php?d=9">N</a></section><nav data-pagination-pages="0" data-pagination-exhausted="true"></nav>',
        course_source_id="42",
        expected_origin="https://moodle.example.edu",
    )
    resources = parse_resource_metadata(
        '<a data-moodle-resource="true" href="https://moodle.example.edu/current/mod/resource/view.php?id=4">R</a>',
        course_source_id="42",
        expected_origin="https://moodle.example.edu",
    )
    assert assignments.records[0].source_id == "assign-cm:7"
    assert announcements.records[0].source_id == "9"
    assert resources[0].source_id == "resource-cm:4"

    entities = parse_frontend_course_entities(
        '<a href="https://moodle.example.edu/current/mod/resource/view.php?id=4">Lecture PDF</a>',
        course_source_id="42",
        expected_origin="https://moodle.example.edu",
    )
    assert entities.remote_files[0].source_id == "resource-cm:4"
    assert entities.remote_files[0].course_source_id == "42"
    assert entities.remote_files[0].title == "Lecture PDF"


@pytest.mark.parametrize("label", ("", "   ", "\x00"))
def test_frontend_resource_parser_rejects_missing_or_unsafe_title(label: str) -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_course_entities

    html = (
        '<a href="https://moodle.example.edu/current/mod/resource/view.php?id=4">' + label + "</a>"
    )
    with pytest.raises(BrowserPolicyError):
        parse_frontend_course_entities(
            html,
            course_source_id="42",
            expected_origin="https://moodle.example.edu",
        )


def test_frontend_resource_parser_rejects_malformed_resource_query() -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_course_entities

    with pytest.raises(BrowserPolicyError):
        parse_frontend_course_entities(
            '<a href="https://moodle.example.edu/current/mod/resource/view.php?id=">Lecture PDF</a>',
            course_source_id="42",
            expected_origin="https://moodle.example.edu",
        )


@pytest.mark.parametrize("label", ("", "   ", "\x00"))
def test_frontend_assignment_parser_rejects_missing_or_unsafe_title(label: str) -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_assignment_list

    html = '<a href="https://moodle.example.edu/current/mod/assign/view.php?id=7">' + label + "</a>"
    with pytest.raises(BrowserPolicyError):
        parse_frontend_assignment_list(
            html,
            course_source_id="42",
            expected_origin="https://moodle.example.edu",
        )


def test_frontend_announcement_parser_retains_canonical_link_title() -> None:
    from aaos.moodle.browser_parsing import parse_frontend_announcement_list

    records = parse_frontend_announcement_list(
        "<h1>Announcements</h1>"
        '<a href="https://moodle.example.edu/current/mod/forum/discuss.php?d=800">Announcement</a>',
        course_source_id="42",
        expected_origin="https://moodle.example.edu",
    )

    assert len(records) == 1
    assert records[0].source_id == "800"
    assert records[0].course_source_id == "42"
    assert records[0].title == "Announcement"


@pytest.mark.parametrize("label", ("", "   ", "\x00"))
def test_frontend_announcement_parser_rejects_missing_or_unsafe_title(label: str) -> None:
    from aaos.moodle.browser_models import BrowserPolicyError
    from aaos.moodle.browser_parsing import parse_frontend_announcement_list

    html = (
        "<h1>Announcements</h1>"
        '<a href="https://moodle.example.edu/current/mod/forum/discuss.php?d=800">' + label + "</a>"
    )

    with pytest.raises(BrowserPolicyError):
        parse_frontend_announcement_list(
            html,
            course_source_id="42",
            expected_origin="https://moodle.example.edu",
        )
