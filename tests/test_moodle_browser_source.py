from __future__ import annotations

from aaos.academic.source import RawCourse
from aaos.moodle.collection import CourseDiscovery, EntityBundle


def test_browser_source_discovers_courses_from_frontend_dom() -> None:
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

        def open_course_detail(self, identifier: int) -> BrowserDocument:
            del identifier
            raise AssertionError("entity route must not be used")

        def open_announcement_list(self, identifier: int) -> BrowserDocument:
            del identifier
            raise AssertionError("entity route must not be used")

        def close(self) -> None:
            return None

    source = MoodleBrowserSource(
        Navigator(), "moodle:fixture:browser-smoke", "https://moodle.example.edu"
    )
    result = discover_courses_for_smoke(source)

    assert isinstance(result, CourseDiscovery)
    assert tuple(course.source_id for course in result.courses) == ("42",)
    assert result.is_complete is True


def test_browser_source_collects_live_proven_course_entities() -> None:
    from aaos.moodle.browser import MoodleBrowserSource
    from aaos.moodle.browser_navigation import BrowserDocument

    class Navigator:
        failure_stage = "NONE"

        def open_course_index_document(self) -> BrowserDocument:
            raise AssertionError("course discovery route must not be used")

        def open_frontend_course_index(self) -> BrowserDocument:
            raise AssertionError("course discovery route must not be used")

        def open_course_detail(self, identifier: int) -> BrowserDocument:
            assert identifier == 42
            return BrowserDocument(
                "COURSE_DETAIL",
                "https://moodle.example.edu",
                "/current/course/view.php",
                "text/html",
                """
                <a href="/current/mod/assign/view.php?id=500">Assignment</a>
                <a href="https://moodle.example.edu/current/mod/assign/view.php?id=500">duplicate</a>
                <a href="/current/mod/forum/view.php?id=700">Announcements</a>
                <a href="/current/mod/resource/view.php?id=900">Lecture PDF</a>
                <a href="/current/mod/resource/view.php?id=900"><span>duplicate</span></a>
                <a href="/current/mod/url/view.php?id=901">External URL</a>
                """,
            )

        def open_announcement_list(self, identifier: int) -> BrowserDocument:
            assert identifier == 700
            return BrowserDocument(
                "ANNOUNCEMENT_LIST",
                "https://moodle.example.edu",
                "/current/mod/forum/view.php",
                "text/html",
                """
                <h1>Announcements</h1>
                <a href="/current/mod/forum/discuss.php?d=800">Announcement</a>
                <a href="/current/mod/forum/discuss.php?d=800"><span>duplicate</span></a>
                """,
            )

        def close(self) -> None:
            return None

    source = MoodleBrowserSource(
        Navigator(), "moodle:fixture:browser", "https://moodle.example.edu"
    )
    result = source.collect_course_entities(RawCourse("42", "Synthetic"))

    assert isinstance(result, EntityBundle)
    assert result.course_source_id == "42"
    assert result.is_complete is True
    assert tuple(record.source_id for record in result.assignments) == ("assign-cm:500",)
    assert tuple(record.title for record in result.assignments) == ("Assignment",)
    assert tuple(record.source_id for record in result.announcements) == ("800",)
    assert tuple(record.title for record in result.announcements) == ("Announcement",)
    assert tuple(record.source_id for record in result.remote_files) == ("resource-cm:900",)
    assert tuple(record.title for record in result.remote_files) == ("Lecture PDF",)


def test_browser_policy_allows_only_get_head_for_fixed_entity_documents() -> None:
    from aaos.moodle.browser_policy import classify_browser_request

    base = "https://moodle.example.edu"
    for path in (
        "/current/course/view.php?id=42",
        "/current/mod/forum/view.php?id=700",
    ):
        assert classify_browser_request("GET", base + path, base_origin=base).code == "ALLOW"
        assert (
            classify_browser_request("POST", base + path, base_origin=base, has_body=True).code
            == "BLOCK"
        )

    assert (
        classify_browser_request(
            "GET", base + "/current/mod/url/view.php?id=901", base_origin=base
        ).code
        == "BLOCK"
    )
