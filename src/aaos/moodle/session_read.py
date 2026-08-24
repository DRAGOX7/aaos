"""Private, session-bound Moodle course-ID read with no generic AJAX surface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

_PAGE_SIZE = 100
_MAX_PAGES = 100
_MAX_COURSES = 10_000
_MAX_REQUEST_BYTES = 4_096
_MAX_RESPONSE_BYTES = 16_384
_OPERATION_NAME = "core_course_get_enrolled_courses_by_timeline_classification"
_AJAX_TIMEOUT_MS = 15_000
_FIXED_BRIDGE_SCRIPT = """async ({offset}) => new Promise((resolve, reject) => {
require(['core/ajax'], function(Ajax) {
  Ajax.call([{methodname: 'core_course_get_enrolled_courses_by_timeline_classification', args: {
    classification: 'allincludinghidden', limit: 100, offset: offset, requiredfields: ['id']
  }}], true, true, true, 15000)[0].then(function(data) {
    if (!data || !Array.isArray(data.courses) || !Number.isInteger(data.nextoffset)) { reject(new Error('invalid')); return; }
    const ids = [];
    for (const course of data.courses) { if (!course || !Number.isInteger(course.id)) { reject(new Error('invalid')); return; } ids.push(course.id); }
    resolve({course_ids: ids, next_offset: data.nextoffset});
  }, function() { reject(new Error('failed')); });
}, function() { reject(new Error('unavailable')); });
})"""


class SessionReadPolicyError(RuntimeError):
    """Raise a fixed error for every rejected private session-read operation."""

    def __init__(self) -> None:
        super().__init__("session-bound Moodle read unavailable")


@dataclass(frozen=True, slots=True)
class SessionCourseReadResult:
    """The sole data shape that may leave the session-bound AJAX transport."""

    course_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _SessionReadOperation:
    """A module-owned authorization token, never a caller-configurable specification."""

    name: str
    ajax: bool
    read_only: bool


_ENROLLED_COURSES_OPERATION = _SessionReadOperation(_OPERATION_NAME, ajax=True, read_only=True)


def authorize_session_read_operation(operation: object) -> None:
    """Accept only the one canonical object owned by this module."""
    if operation is not _ENROLLED_COURSES_OPERATION:
        raise SessionReadPolicyError()


class OneShotMoodleAjaxPermit:
    """Authorize exactly one fixed Moodle AJAX request for one validated offset."""

    def __init__(self, canonical_wwwroot: str) -> None:
        parsed = urlsplit(canonical_wwwroot)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.path.rstrip("/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise SessionReadPolicyError()
        self._canonical_wwwroot = canonical_wwwroot.rstrip("/")
        self._expected_offset: int | None = None

    def arm(self, offset: int) -> None:
        if self._expected_offset is not None or not _nonnegative_int(offset):
            raise SessionReadPolicyError()
        self._expected_offset = offset

    def consume(self, method: object, url: object, resource_type: object, body: object) -> None:
        offset = self._expected_offset
        if offset is None:
            raise SessionReadPolicyError()
        if not _is_exact_ajax_target(url, self._canonical_wwwroot):
            raise SessionReadPolicyError()
        if method != "POST" or resource_type not in {"xhr", "fetch"}:
            raise SessionReadPolicyError()
        if not _is_exact_operation_body(body, offset):
            raise SessionReadPolicyError()
        self._expected_offset = None

    @property
    def consumed(self) -> bool:
        """Expose only whether the current one-request capability was consumed."""
        return self._expected_offset is None


class _Route(Protocol):
    def abort(self) -> None: ...

    def continue_(self) -> None: ...


class _Request(Protocol):
    method: str
    url: str
    resource_type: str
    post_data: str | None


class _Page(Protocol):
    url: str

    def goto(self, url: str, *, wait_until: str, timeout: int) -> object: ...

    def evaluate(self, expression: str, arg: object) -> object: ...

    def on(self, event: str, handler: object) -> None: ...

    def close(self) -> None: ...


class _Context(Protocol):
    pages: list[_Page]

    def route(self, pattern: str, handler: object) -> None: ...

    def new_page(self) -> _Page: ...


class _SessionBoundMoodleCourseRead:
    """Own the sole fixed browser-session course read; callers receive IDs only."""

    def __init__(
        self, context: _Context, *, base_origin: str, canonical_wwwroot: str, sentinel: _Page
    ) -> None:
        from aaos.moodle.browser_models import BrowserPolicyError

        if not canonical_wwwroot.startswith(base_origin + "/") or sentinel.url != "about:blank":
            raise BrowserPolicyError()
        self._context = context
        self._base_origin = base_origin
        self._canonical_wwwroot = canonical_wwwroot.rstrip("/")
        self._sentinel: _Page | None = sentinel
        self._permit: OneShotMoodleAjaxPermit | None = None
        self._policy_failed = False
        context.route("**/*", self._handle_request)

    def discover_enrolled_course_ids(self) -> tuple[int, ...]:
        """Return only a bounded, complete traversal of numeric Moodle course IDs."""
        from aaos.moodle.browser_models import BrowserPolicyError

        page: _Page | None = None
        try:
            page = self._context.new_page()
            for event in ("popup", "download", "websocket", "filechooser"):
                page.on(event, self._blocked_event)
            self._complete_transition(page)
            target = self._canonical_wwwroot + "/my/courses.php"
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            if page.url != target or self._policy_failed:
                raise SessionReadPolicyError()

            def fetch(offset: int) -> object:
                permit = OneShotMoodleAjaxPermit(self._canonical_wwwroot)
                permit.arm(offset)
                self._permit = permit
                result = page.evaluate(_FIXED_BRIDGE_SCRIPT, {"offset": offset})
                if self._policy_failed or not permit.consumed:
                    raise SessionReadPolicyError()
                return result

            return collect_course_ids(fetch)
        except (AttributeError, OSError, PlaywrightError, TimeoutError, TypeError, ValueError):
            raise SessionReadPolicyError() from None
        except BrowserPolicyError:
            raise SessionReadPolicyError() from None
        finally:
            if page is not None:
                try:
                    page.close()
                except (AttributeError, OSError, PlaywrightError, TypeError, ValueError):
                    # The context owner remains responsible for final lifecycle cleanup.
                    pass

    def _handle_request(self, route: _Route, request: _Request) -> None:
        from aaos.moodle.browser_policy import classify_browser_request

        permit = self._permit
        try:
            if permit is None:
                raise SessionReadPolicyError()
            permit.consume(
                request.method,
                request.url,
                request.resource_type,
                getattr(request, "post_data", None),
            )
        except SessionReadPolicyError:
            decision = classify_browser_request(
                request.method,
                request.url,
                base_origin=self._base_origin,
                canonical_wwwroot=self._canonical_wwwroot,
                resource_type=request.resource_type,
                has_body=getattr(request, "post_data", None) is not None,
            )
            if decision.code != "ALLOW":
                # Static bootstrap resources remain independently fail-closed; a blocked
                # optional resource must not turn the approved document navigation into
                # a generic AJAX authorization failure.  Network-capable requests do.
                if request.method != "GET" or request.resource_type in {"xhr", "fetch"}:
                    self._policy_failed = True
                route.abort()
                return
        route.continue_()

    @staticmethod
    def _blocked_event(*_args: object) -> None:
        raise SessionReadPolicyError()

    def _complete_transition(self, page: _Page) -> None:
        sentinel = self._sentinel
        if (
            sentinel is None
            or self._context.pages != [sentinel, page]
            or sentinel.url != "about:blank"
        ):
            raise SessionReadPolicyError()
        sentinel.close()
        if self._context.pages != [page]:
            raise SessionReadPolicyError()
        self._sentinel = None


def collect_course_ids(fetch_page: Callable[[int], object]) -> tuple[int, ...]:
    """Traverse the approved all-enrolled-course operation with a bounded proof."""
    offset = 0
    seen: set[int] = set()
    for _page in range(_MAX_PAGES):
        course_ids, next_offset = _validate_reduced_response(fetch_page(offset))
        if not course_ids:
            return tuple(sorted(seen))
        if next_offset <= offset or next_offset - offset != len(course_ids):
            raise SessionReadPolicyError()
        if any(course_id in seen for course_id in course_ids):
            raise SessionReadPolicyError()
        seen.update(course_ids)
        if len(seen) > _MAX_COURSES:
            raise SessionReadPolicyError()
        offset = next_offset
    raise SessionReadPolicyError()


def _is_exact_ajax_target(url: object, canonical_wwwroot: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        target = urlsplit(url)
        base = urlsplit(canonical_wwwroot)
    except ValueError:
        return False
    if (
        target.scheme != base.scheme
        or target.netloc != base.netloc
        or target.fragment
        or target.username
        or target.password
        or target.path != base.path.rstrip("/") + "/lib/ajax/service.php"
    ):
        return False
    parts = target.query.split("&")
    sesskey_parts = [
        part for part in parts if part.startswith("sesskey=") and len(part) > len("sesskey=")
    ]
    return (
        len(parts) == 3
        and f"info={_OPERATION_NAME}" in parts
        and "nosessionupdate=true" in parts
        and len(sesskey_parts) == 1
    )


def _is_exact_operation_body(body: object, offset: int) -> bool:
    if not isinstance(body, str) or len(body.encode("utf-8")) > _MAX_REQUEST_BYTES:
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, list):
        return False
    return bool(
        payload
        == [
            {
                "index": 0,
                "methodname": _OPERATION_NAME,
                "args": {
                    "classification": "allincludinghidden",
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "requiredfields": ["id"],
                },
            }
        ]
    )


def _validate_reduced_response(response: object) -> tuple[tuple[int, ...], int]:
    try:
        encoded = json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise SessionReadPolicyError() from None
    if len(encoded) > _MAX_RESPONSE_BYTES or not isinstance(response, dict):
        raise SessionReadPolicyError()
    if set(response) != {"course_ids", "next_offset"}:
        raise SessionReadPolicyError()
    values = response["course_ids"]
    next_offset = response["next_offset"]
    if (
        not isinstance(values, list)
        or len(values) > _PAGE_SIZE
        or not _nonnegative_int(next_offset)
        or not all(_positive_int(value) for value in values)
    ):
        raise SessionReadPolicyError()
    return tuple(values), next_offset


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
