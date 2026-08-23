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


def test_assignment_announcement_and_resource_ids_are_typed() -> None:
    from aaos.moodle.browser_parsing import (
        parse_announcement_list,
        parse_assignment_list,
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
