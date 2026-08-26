from __future__ import annotations

from aaos.academic.source import RawCourse
from aaos.moodle.browser import MoodleBrowserSource
from aaos.moodle.browser_navigation import BrowserDocument
from aaos.moodle.collection import EntityBundle

_ORIGIN = "https://moodle.example.edu"


class _Navigator:
    failure_stage = "NONE"

    def open_course_index_document(self) -> BrowserDocument:
        raise AssertionError("course discovery route must not be used")

    def open_frontend_course_index(self) -> BrowserDocument:
        raise AssertionError("course discovery route must not be used")

    def open_course_detail(self, identifier: int) -> BrowserDocument:
        assert identifier == 42
        return BrowserDocument(
            "COURSE_DETAIL",
            _ORIGIN,
            "/current/course/view.php",
            "text/html",
            """
            <html>
              <body>
                <a href="https://moodle.example.edu/current/mod/assign/view.php?id=500">
                  Assignment
                </a>
                <a href="https://moodle.example.edu/current/mod/assign/view.php?id=500">
                  Duplicate assignment link
                </a>
                <a href="https://moodle.example.edu/current/mod/resource/view.php?id=900">Lecture PDF</a>
              </body>
            </html>
            """,
        )

    def open_announcement_list(self, identifier: int) -> BrowserDocument:
        raise AssertionError(
            f"announcement route must not be used without a forum candidate: {identifier}"
        )

    def close(self) -> None:
        return None


def test_browser_source_collects_assignments_from_course_detail_without_assignment_index() -> None:
    navigator = _Navigator()
    source = MoodleBrowserSource(
        navigator,
        "moodle:fixture:browser-smoke",
        _ORIGIN,
    )

    result = source.collect_course_entities(RawCourse("42", ""))

    assert isinstance(result, EntityBundle)
    assert tuple(record.source_id for record in result.assignments) == ("assign-cm:500",)
    assert tuple(record.source_id for record in result.remote_files) == ("resource-cm:900",)
    assert tuple(record.title for record in result.remote_files) == ("Lecture PDF",)
    assert result.announcements == ()
    assert result.is_complete is True
