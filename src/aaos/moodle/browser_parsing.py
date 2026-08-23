"""Pure parsers for sanitized Moodle browser documents."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Generic, TypeVar
from urllib.parse import parse_qs, urlsplit

from aaos.academic.source import RawAnnouncement, RawAssignment, RawCourse, RawRemoteFile
from aaos.moodle.browser_models import (
    BrowserPolicyError,
    PaginationProof,
    validate_pagination_proof,
)

Record = TypeVar("Record")


@dataclass(frozen=True, slots=True)
class ParsedPage(Generic[Record]):
    records: tuple[Record, ...]
    proof: PaginationProof


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.body: dict[str, str] | None = None
        self.links: list[dict[str, str]] = []
        self.pagination: dict[str, str] | None = None
        self.in_announcement_forum = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
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


def _parse(html: str) -> _Markup:
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
        or not query[key][0].isdigit()
    ):
        raise BrowserPolicyError()
    value = int(query[key][0])
    if value <= 0:
        raise BrowserPolicyError()
    return str(value)


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
