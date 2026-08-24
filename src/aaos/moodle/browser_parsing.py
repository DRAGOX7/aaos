"""Pure parsers for sanitized Moodle browser documents."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Generic, Literal, TypeAlias, TypeVar
from urllib.parse import parse_qs, urlsplit

from aaos.academic.source import RawAnnouncement, RawAssignment, RawCourse, RawRemoteFile
from aaos.moodle.browser_models import (
    BrowserPolicyError,
    PaginationProof,
    validate_pagination_proof,
)

Record = TypeVar("Record")
CourseLinkShape: TypeAlias = Literal["COURSE_VIEW_ID_QUERY", "OTHER_SAME_ORIGIN", "MIXED", "NONE"]


@dataclass(frozen=True, slots=True)
class ParsedPage(Generic[Record]):
    records: tuple[Record, ...]
    proof: PaginationProof


@dataclass(frozen=True, slots=True)
class CourseIndexStructureDiagnostic:
    """Fixed, non-content evidence about one COURSE_INDEX document."""

    page_identity: Literal["MY_COURSES", "OTHER"]
    course_container_count: int
    candidate_course_link_count: int
    numeric_course_id_link_count: int
    course_data_attribute_count: int
    pagination_container_count: int
    next_page_link_count: int
    empty_state_marker: bool
    known_fixture_schema_match: bool
    course_link_shape: CourseLinkShape

    def render(self) -> str:
        """Render only safe structural labels and counts."""
        return "\n".join(
            (
                "COURSE_INDEX_STRUCTURE_DIAGNOSTIC",
                f"PAGE_IDENTITY={self.page_identity}",
                f"COURSE_CONTAINER_COUNT={self.course_container_count}",
                f"CANDIDATE_COURSE_LINK_COUNT={self.candidate_course_link_count}",
                f"NUMERIC_COURSE_ID_LINK_COUNT={self.numeric_course_id_link_count}",
                f"COURSE_DATA_ATTRIBUTE_COUNT={self.course_data_attribute_count}",
                f"PAGINATION_CONTAINER_COUNT={self.pagination_container_count}",
                f"NEXT_PAGE_LINK_COUNT={self.next_page_link_count}",
                f"EMPTY_STATE_MARKER={'YES' if self.empty_state_marker else 'NO'}",
                f"KNOWN_FIXTURE_SCHEMA_MATCH={'YES' if self.known_fixture_schema_match else 'NO'}",
                f"COURSE_LINK_SHAPE={self.course_link_shape}",
            )
        )


@dataclass(frozen=True, slots=True)
class CourseIndexLoadingDiagnostic:
    """Fixed, non-content evidence about bounded COURSE_INDEX settling."""

    initial: CourseIndexStructureDiagnostic
    settled: CourseIndexStructureDiagnostic

    def render(self) -> str:
        """Render only fixed labels and aggregate structural counts."""
        return "\n".join(
            (
                "COURSE_INDEX_LOADING_DIAGNOSTIC",
                f"PAGE_IDENTITY={self.settled.page_identity}",
                f"INITIAL_COURSE_LINK_COUNT={self.initial.candidate_course_link_count}",
                f"SETTLED_COURSE_LINK_COUNT={self.settled.candidate_course_link_count}",
                f"INITIAL_NUMERIC_ID_COUNT={self.initial.numeric_course_id_link_count}",
                f"SETTLED_NUMERIC_ID_COUNT={self.settled.numeric_course_id_link_count}",
                f"INITIAL_EMPTY_MARKER={'YES' if self.initial.empty_state_marker else 'NO'}",
                f"SETTLED_EMPTY_MARKER={'YES' if self.settled.empty_state_marker else 'NO'}",
                f"DOM_CHANGED_AFTER_LOAD={'YES' if self.initial != self.settled else 'NO'}",
            )
        )


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.body: dict[str, str] | None = None
        self.links: list[dict[str, str]] = []
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.pagination: dict[str, str] | None = None
        self.in_announcement_forum = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        self.elements.append((tag, values))
        if tag == "body":
            self.body = values
        if tag == "section" and values.get("data-announcement-forum") == "true":
            self.in_announcement_forum = True
        if tag == "a":
            values["_announcement"] = "true" if self.in_announcement_forum else "false"
            self.links.append(values)
        if tag == "nav" and "data-pagination-pages" in values:
            self.pagination = values

    def handle_endtag(self, tag: str) -> None:
        if tag == "section":
            self.in_announcement_forum = False


def parse_course_index(html: str, *, expected_origin: str) -> ParsedPage[RawCourse]:
    markup = _parse(html)
    courses = tuple(
        RawCourse(
            source_id=_identifier(link, "/current/course/view.php", "id", expected_origin), title=""
        )
        for link in markup.links
        if link.get("data-moodle-course") == "true"
    )
    _unique((course.source_id for course in courses))
    return ParsedPage(courses, _proof("COURSE_INDEX", markup, len(courses)))


def parse_frontend_course_index(html: object, *, expected_origin: str) -> tuple[RawCourse, ...]:
    """Extract only stable IDs from the live-proven Moodle course-link DOM shape."""
    markup = _parse(html)
    identifiers: list[str] = []
    for link in markup.links:
        identifier = _frontend_course_identifier(link, expected_origin)
        if identifier is not None:
            identifiers.append(identifier)
    _unique(identifiers)
    if len(identifiers) > 10_000:
        raise BrowserPolicyError()
    return tuple(RawCourse(source_id=identifier, title="") for identifier in identifiers)


def diagnose_course_index_structure(
    html: str, *, expected_origin: str
) -> CourseIndexStructureDiagnostic:
    """Summarize course-index shape without exposing document content or identities."""
    markup = _parse(html)
    candidate_links = tuple(
        link for link in markup.links if _is_course_view_link(link, expected_origin)
    )

    numeric_links = tuple(
        link for link in candidate_links if _has_numeric_course_id(link, expected_origin)
    )
    known_fixture_schema_match = _matches_known_course_index_schema(markup, expected_origin)
    return CourseIndexStructureDiagnostic(
        page_identity=("MY_COURSES" if has_authenticated_my_courses_page(html) else "OTHER"),
        course_container_count=sum(
            1 for _tag, values in markup.elements if values.get("data-moodle-course") == "true"
        ),
        candidate_course_link_count=len(candidate_links),
        numeric_course_id_link_count=len(numeric_links),
        course_data_attribute_count=sum(
            1
            for _tag, values in markup.elements
            if "data-course-id" in values or "data-courseid" in values
        ),
        pagination_container_count=sum(
            1
            for tag, values in markup.elements
            if tag == "nav"
            and (
                "data-pagination-pages" in values
                or "pagination" in frozenset(values.get("class", "").split())
            )
        ),
        next_page_link_count=sum(
            1 for link in markup.links if "next" in frozenset(link.get("rel", "").split())
        ),
        empty_state_marker=any(
            any(
                token in {"empty", "empty-state", "nocourses", "no-courses"}
                for token in values.get("class", "").split()
            )
            or values.get("data-region") in {"no-courses", "empty-state"}
            for _tag, values in markup.elements
        ),
        known_fixture_schema_match=known_fixture_schema_match,
        course_link_shape=_course_link_shape(candidate_links, numeric_links),
    )


def diagnose_course_index_loading(
    samples: tuple[str, str, str], *, expected_origin: str
) -> CourseIndexLoadingDiagnostic:
    """Compare bounded, in-memory structural samples without exposing document content."""
    initial, _intermediate, settled = (
        diagnose_course_index_structure(sample, expected_origin=expected_origin)
        for sample in samples
    )
    return CourseIndexLoadingDiagnostic(initial=initial, settled=settled)


def has_authenticated_my_courses_page(html: object) -> bool:
    """Return whether markup has the stable Moodle My Courses page identity."""
    if not isinstance(html, str):
        return False
    markup = _parse(html)
    if markup.body is None:
        return False
    classes = frozenset(markup.body.get("class", "").split())
    return "page-mycourses" in classes and not classes.intersection(
        {"page-login-index", "page-login", "page-auth"}
    )


def has_frontend_empty_course_state(html: object) -> bool:
    """Recognize only Moodle's explicit no-courses element, never text content."""
    if not isinstance(html, str) or not has_authenticated_my_courses_page(html):
        return False
    markup = _parse(html)
    return any(values.get("data-region") == "no-courses" for _tag, values in markup.elements)


def parse_assignment_list(
    html: str, *, course_source_id: str, expected_origin: str
) -> ParsedPage[RawAssignment]:
    markup = _parse(html)
    assignments = tuple(
        RawAssignment(
            source_id=f"assign-cm:{_identifier(link, '/current/mod/assign/view.php', 'id', expected_origin)}",
            course_source_id=course_source_id,
            title="",
        )
        for link in markup.links
        if link.get("data-moodle-assignment") == "true"
    )
    _unique((record.source_id for record in assignments))
    return ParsedPage(assignments, _proof("ASSIGNMENT_LIST", markup, len(assignments)))


def parse_announcement_list(
    html: str, *, course_source_id: str, expected_origin: str
) -> ParsedPage[RawAnnouncement]:
    markup = _parse(html)
    announcements = tuple(
        RawAnnouncement(
            source_id=_identifier(link, "/current/mod/forum/discuss.php", "d", expected_origin),
            course_source_id=course_source_id,
            title="",
        )
        for link in markup.links
        if link.get("_announcement") == "true"
    )
    _unique((record.source_id for record in announcements))
    return ParsedPage(announcements, _proof("ANNOUNCEMENT_LIST", markup, len(announcements)))


def parse_resource_metadata(
    html: str, *, course_source_id: str, expected_origin: str
) -> tuple[RawRemoteFile, ...]:
    markup = _parse(html)
    resources = tuple(
        RawRemoteFile(
            source_id=f"resource-cm:{_identifier(link, '/current/mod/resource/view.php', 'id', expected_origin)}",
            course_source_id=course_source_id,
            title="",
            remote_url=None,
        )
        for link in markup.links
        if link.get("data-moodle-resource") == "true"
    )
    _unique((record.source_id for record in resources))
    return resources


def _parse(html: object) -> _Markup:
    if not isinstance(html, str):
        raise BrowserPolicyError()
    markup = _Markup()
    try:
        markup.feed(html)
        markup.close()
    except (ValueError, AssertionError):
        raise BrowserPolicyError() from None
    return markup


def _identifier(link: dict[str, str], expected_path: str, key: str, expected_origin: str) -> str:
    href = link.get("href")
    if not href:
        raise BrowserPolicyError()
    parsed = urlsplit(href)
    if f"{parsed.scheme}://{parsed.netloc}" != expected_origin or parsed.path != expected_path:
        raise BrowserPolicyError()
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.fragment
        or set(query) != {key}
        or len(query[key]) != 1
        or not _is_bounded_ascii_positive_decimal(query[key][0])
    ):
        raise BrowserPolicyError()
    value = int(query[key][0])
    if value <= 0:
        raise BrowserPolicyError()
    return str(value)


def _is_bounded_ascii_positive_decimal(value: str) -> bool:
    """Accept stable numeric Moodle IDs without Unicode or unbounded integer input."""
    return 1 <= len(value) <= 10 and value.isascii() and value.isdecimal() and value != "0"


def _frontend_course_identifier(link: dict[str, str], expected_origin: str) -> str | None:
    """Accept only the canonical course URL shape emitted by the live Moodle page."""
    href = link.get("href")
    if not href:
        return None
    try:
        parsed = urlsplit(href)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        if f"{parsed.scheme}://{parsed.netloc}" != expected_origin:
            return None
    elif not parsed.path.startswith("/"):
        return None
    if parsed.path != "/current/course/view.php":
        return None
    try:
        return _identifier(
            {"href": expected_origin + href if not parsed.scheme else href},
            "/current/course/view.php",
            "id",
            expected_origin,
        )
    except BrowserPolicyError:
        raise BrowserPolicyError() from None


def _is_course_view_link(link: dict[str, str], expected_origin: str) -> bool:
    href = link.get("href")
    if not href:
        return False
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    return (
        f"{parsed.scheme}://{parsed.netloc}" == expected_origin
        and parsed.path == "/current/course/view.php"
    )


def _has_numeric_course_id(link: dict[str, str], expected_origin: str) -> bool:
    try:
        _identifier(link, "/current/course/view.php", "id", expected_origin)
    except BrowserPolicyError:
        return False
    return True


def _course_link_shape(
    candidate_links: tuple[dict[str, str], ...], numeric_links: tuple[dict[str, str], ...]
) -> CourseLinkShape:
    if not candidate_links:
        return "NONE"
    if len(candidate_links) == len(numeric_links):
        return "COURSE_VIEW_ID_QUERY"
    if numeric_links:
        return "MIXED"
    return "OTHER_SAME_ORIGIN"


def _matches_known_course_index_schema(markup: _Markup, expected_origin: str) -> bool:
    """Check the existing parser contract without creating course records."""
    try:
        identifiers = tuple(
            _identifier(link, "/current/course/view.php", "id", expected_origin)
            for link in markup.links
            if link.get("data-moodle-course") == "true"
        )
        _unique(identifiers)
        _proof("COURSE_INDEX", markup, len(identifiers))
    except BrowserPolicyError:
        return False
    return True


def _proof(route: str, markup: _Markup, count: int) -> PaginationProof:
    if markup.pagination is None:
        raise BrowserPolicyError()
    try:
        pages = tuple(int(part) for part in markup.pagination["data-pagination-pages"].split(","))
    except ValueError:
        raise BrowserPolicyError() from None
    proof = PaginationProof(
        route=route,  # type: ignore[arg-type]
        visited_page_numbers=pages,
        exhausted=markup.pagination.get("data-pagination-exhausted") == "true",
        expected_record_count=None,
        observed_record_count=count,
    )
    validate_pagination_proof(proof)
    return proof


def _unique(values: Iterable[str]) -> None:
    identifiers: tuple[str, ...] = tuple(values)
    if len(identifiers) != len(set(identifiers)):
        raise BrowserPolicyError()
