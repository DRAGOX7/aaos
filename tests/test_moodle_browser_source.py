from __future__ import annotations

from aaos.academic.source import RawCourse, SourceCollectionFailure
from aaos.moodle.collection import CourseDiscovery


def test_course_only_source_discovers_courses_and_blocks_entity_collection() -> None:
    from aaos.moodle.browser import MoodleBrowserSource, discover_courses_for_smoke
    from aaos.moodle.browser_navigation import BrowserDocument

    class Navigator:
        def open_course_index_document(self) -> BrowserDocument:
            raise AssertionError("diagnostic route must not be used")

        def open(self, _route: str, *, identifier: int | None = None) -> BrowserDocument:
            del identifier
            raise AssertionError("legacy static DOM route must not be used")

        def open_frontend_course_index(self) -> BrowserDocument:
            return BrowserDocument(
                "COURSE_INDEX",
                "https://moodle.example.edu",
                "/current/my/courses.php",
                "text/html",
                "<a href='/current/course/view.php?id=42'>Synthetic</a>",
            )

        def close(self) -> None:
            return None

    source = MoodleBrowserSource(
        Navigator(), "moodle:fixture:browser-smoke", "https://moodle.example.edu"
    )
    result = discover_courses_for_smoke(source)

    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert result.is_complete is True
    assert source.collect_course_entities(RawCourse("42", "Synthetic")) == SourceCollectionFailure(
        "moodle:fixture:browser-smoke", "browser_entity_scope_not_approved"
    )
