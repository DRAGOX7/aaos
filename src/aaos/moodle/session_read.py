"""Private, session-bound Moodle course-ID read with no generic AJAX surface."""

from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Callable, Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

_PAGE_SIZE = 100
_MAX_PAGES = 100
_MAX_COURSES = 10_000
_MAX_RESPONSE_BYTES = 16_384
_OPERATION_NAME = "core_course_get_enrolled_courses_by_timeline_classification"
_AJAX_TIMEOUT_MS = 15_000
_PROVENANCE_HEADER = "X-AAOS-Request-Provenance"
_FIXED_BRIDGE_SCRIPT = """async ({offset, canonicalWwwroot, provenanceNonce}) => {
const config = globalThis.M && globalThis.M.cfg;
const sesskey = config && typeof config.sesskey === 'string' ? config.sesskey : null;
if (!sesskey) { return {bridge_failure: 'SESSION_KEY_UNAVAILABLE'}; }
let endpoint;
try { endpoint = new URL('lib/ajax/service.php', canonicalWwwroot + '/'); }
catch (_error) { return {bridge_failure: 'TRANSPORT_FAILED'}; }
const query = new URLSearchParams({
  sesskey: sesskey,
  info: 'core_course_get_enrolled_courses_by_timeline_classification',
  nosessionupdate: 'true'
});
let response;
const controller = new AbortController();
const timeoutId = setTimeout(() => controller.abort(), 15000);
try {
  response = await fetch(endpoint.toString() + '?' + query.toString(), {
    method: 'POST', credentials: 'same-origin', redirect: 'error',
    signal: controller.signal,
    headers: {
      'Content-Type': 'application/json',
      'X-AAOS-Request-Provenance': provenanceNonce
    },
    body: JSON.stringify([{index: 0, methodname: 'core_course_get_enrolled_courses_by_timeline_classification', args: {
      classification: 'allincludinghidden', limit: 100, offset: offset, requiredfields: ['id']
    }}])
  });
} catch (_error) { return {bridge_failure: 'TRANSPORT_FAILED'}; }
finally { clearTimeout(timeoutId); }
if (!response.ok) { return {bridge_failure: 'MOODLE_ERROR'}; }
let payload;
try { payload = await response.json(); }
catch (_error) { return {bridge_failure: 'MALFORMED'}; }
if (!Array.isArray(payload) || payload.length !== 1 || !payload[0] || typeof payload[0] !== 'object') {
  return {bridge_failure: 'MALFORMED'};
}
const item = payload[0];
if (item.error === true || item.exception) { return {bridge_failure: 'MOODLE_ERROR'}; }
const data = item.data;
if (!data || !Array.isArray(data.courses) || !Number.isInteger(data.nextoffset)) {
  return {bridge_failure: 'MALFORMED'};
}
const ids = [];
for (const course of data.courses) {
  if (!course || !Number.isInteger(course.id) || course.id <= 0) {
    return {bridge_failure: 'MALFORMED'};
  }
  ids.push(course.id);
}
return {course_ids: ids, next_offset: data.nextoffset};
}"""


class SessionReadPolicyError(RuntimeError):
    """Raise a fixed error for every rejected private session-read operation."""

    def __init__(self) -> None:
        super().__init__("session-bound Moodle read unavailable")


@dataclass(frozen=True, slots=True)
class SessionReadDiagnostic:
    """Fixed, non-secret state observed at existing session-read boundaries."""

    authenticated_page: str
    sentinel_transition: str
    collection_page: str
    bridge_stage: str
    ajax_permit_stage: str
    ajax_permit_rejection_reason: str
    ajax_response_stage: str
    pagination_stage: str
    root_cause: str


def render_session_read_diagnostic(diagnostic: SessionReadDiagnostic) -> str:
    """Render only application-owned diagnostic enums, never runtime values."""
    return "\n".join(
        (
            "SESSION_READ_DIAGNOSTIC",
            f"AUTHENTICATED_PAGE={diagnostic.authenticated_page}",
            f"SENTINEL_TRANSITION={diagnostic.sentinel_transition}",
            f"COLLECTION_PAGE={diagnostic.collection_page}",
            f"BRIDGE_STAGE={diagnostic.bridge_stage}",
            f"AJAX_PERMIT_STAGE={diagnostic.ajax_permit_stage}",
            f"AJAX_PERMIT_REJECTION_REASON={diagnostic.ajax_permit_rejection_reason}",
            f"AJAX_RESPONSE_STAGE={diagnostic.ajax_response_stage}",
            f"PAGINATION_STAGE={diagnostic.pagination_stage}",
            f"SESSION_READ_ROOT_CAUSE={diagnostic.root_cause}",
        )
    )


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


class _OneShotMoodleAjaxPermit:
    """Authorize exactly one fixed Moodle AJAX request for one validated offset."""

    def __init__(self, canonical_wwwroot: str, provenance_nonce: str) -> None:
        parsed = urlsplit(canonical_wwwroot)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.path.rstrip("/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not provenance_nonce
        ):
            raise SessionReadPolicyError()
        self._canonical_wwwroot = canonical_wwwroot.rstrip("/")
        self._provenance_nonce: str | None = provenance_nonce
        self._expected_offset: int | None = None

    def arm(self, offset: int) -> None:
        if self._expected_offset is not None or not _nonnegative_int(offset):
            raise SessionReadPolicyError()
        self._expected_offset = offset

    def consume(self, method: object, url: object, headers: object) -> dict[str, str]:
        nonce = self._provenance_nonce
        if self._expected_offset is None or nonce is None:
            raise SessionReadPolicyError()
        if method != "POST" or not _is_canonical_ajax_endpoint(url, self._canonical_wwwroot):
            raise SessionReadPolicyError()
        received_nonce, forwarded_headers = _extract_and_strip_provenance_header(headers)
        if received_nonce is None or not hmac.compare_digest(received_nonce, nonce):
            raise SessionReadPolicyError()
        self._expected_offset = None
        self._provenance_nonce = None
        return forwarded_headers

    @property
    def consumed(self) -> bool:
        """Expose only whether the current one-request capability was consumed."""
        return self._expected_offset is None


class _Route(Protocol):
    def abort(self) -> None: ...

    def continue_(self, *, headers: dict[str, str] | None = None) -> None: ...


class _Request(Protocol):
    method: str
    url: str
    resource_type: str
    post_data: str | None
    headers: dict[str, str]


class _Response(Protocol):
    url: str
    status: int


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
        self._permit: _OneShotMoodleAjaxPermit | None = None
        self._policy_failed = False
        self._diagnostic = SessionReadDiagnostic(
            authenticated_page="PASS",
            sentinel_transition="NOT_REACHED",
            collection_page="NOT_REACHED",
            bridge_stage="NOT_REACHED",
            ajax_permit_stage="NOT_REACHED",
            ajax_permit_rejection_reason="NOT_APPLICABLE",
            ajax_response_stage="NOT_REACHED",
            pagination_stage="NOT_REACHED",
            root_cause="NOT_REACHED",
        )
        context.route("**/*", self._handle_request)

    @property
    def diagnostic(self) -> SessionReadDiagnostic:
        """Return fixed diagnostic classifications only."""
        return self._diagnostic

    def diagnose_enrolled_course_ids(self) -> SessionReadDiagnostic:
        """Exercise the production read once while returning only safe classifications."""
        try:
            self.discover_enrolled_course_ids()
        except SessionReadPolicyError:
            if self._diagnostic.root_cause == "NOT_REACHED":
                if self._diagnostic.ajax_response_stage == "REDUCED_SCHEMA_PASS":
                    self._diagnostic = replace(
                        self._diagnostic,
                        pagination_stage="PAGINATION_VALIDATION_FAILED",
                        root_cause="PAGINATION_VALIDATION_FAILED",
                    )
                else:
                    self._diagnostic = replace(
                        self._diagnostic, root_cause="BRIDGE_EXECUTION_FAILED"
                    )
        else:
            self._diagnostic = replace(
                self._diagnostic,
                pagination_stage="PASS",
                root_cause="NO_FAILURE",
            )
        return self._diagnostic

    def discover_enrolled_course_ids(self) -> tuple[int, ...]:
        """Return only a bounded, complete traversal of numeric Moodle course IDs."""
        from aaos.moodle.browser_models import BrowserPolicyError

        page: _Page | None = None
        try:
            page = self._context.new_page()
            self._diagnostic = replace(self._diagnostic, collection_page="PASS")
            for event in ("popup", "download", "websocket", "filechooser"):
                page.on(event, self._blocked_event)
            page.on("response", self._handle_response)
            self._complete_transition(page)
            target = self._canonical_wwwroot + "/my/courses.php"
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            if page.url != target or self._policy_failed:
                raise SessionReadPolicyError()

            def fetch(offset: int) -> object:
                provenance_nonce = secrets.token_urlsafe(32)
                permit = _OneShotMoodleAjaxPermit(self._canonical_wwwroot, provenance_nonce)
                permit.arm(offset)
                self._permit = permit
                self._diagnostic = replace(
                    self._diagnostic,
                    bridge_stage="BRIDGE_EXECUTION_FAILED",
                    ajax_permit_stage="REQUEST_NOT_OBSERVED",
                    ajax_response_stage="TRANSPORT_FAILED",
                )
                try:
                    result = page.evaluate(
                        _FIXED_BRIDGE_SCRIPT,
                        {
                            "offset": offset,
                            "canonicalWwwroot": self._canonical_wwwroot,
                            "provenanceNonce": provenance_nonce,
                        },
                    )
                except TimeoutError:
                    self._diagnostic = replace(
                        self._diagnostic, bridge_stage="TIMEOUT", root_cause="TIMEOUT"
                    )
                    raise SessionReadPolicyError() from None
                except (OSError, PlaywrightError, TypeError, ValueError):
                    raise SessionReadPolicyError() from None
                if self._policy_failed:
                    raise SessionReadPolicyError()
                if isinstance(result, dict) and result.get("bridge_failure") in {
                    "MALFORMED",
                    "MOODLE_ERROR",
                    "SESSION_KEY_UNAVAILABLE",
                    "TRANSPORT_FAILED",
                }:
                    if self._diagnostic.ajax_permit_stage == "REQUEST_REJECTED":
                        raise SessionReadPolicyError()
                    failure = result["bridge_failure"]
                    if failure == "SESSION_KEY_UNAVAILABLE":
                        self._diagnostic = replace(
                            self._diagnostic,
                            bridge_stage="SESSION_KEY_UNAVAILABLE",
                            ajax_response_stage="NOT_REACHED",
                            root_cause="SESSION_KEY_UNAVAILABLE",
                        )
                    elif failure == "MOODLE_ERROR":
                        self._diagnostic = replace(
                            self._diagnostic,
                            ajax_response_stage="MOODLE_ERROR",
                            root_cause="MOODLE_ERROR",
                        )
                    elif failure == "TRANSPORT_FAILED":
                        self._diagnostic = replace(
                            self._diagnostic,
                            ajax_response_stage="TRANSPORT_FAILED",
                            root_cause="TRANSPORT_FAILED",
                        )
                    else:
                        self._diagnostic = replace(
                            self._diagnostic,
                            ajax_response_stage="MALFORMED",
                            root_cause="MALFORMED_RESPONSE",
                        )
                    raise SessionReadPolicyError()
                if self._policy_failed or not permit.consumed:
                    if not permit.consumed:
                        self._diagnostic = replace(
                            self._diagnostic,
                            ajax_permit_stage="REQUEST_NOT_OBSERVED",
                            root_cause="AJAX_REQUEST_NOT_OBSERVED",
                        )
                    raise SessionReadPolicyError()
                self._diagnostic = replace(
                    self._diagnostic,
                    bridge_stage="PASS",
                    ajax_response_stage="REDUCED_SCHEMA_PASS",
                )
                return result

            return collect_course_ids(fetch)
        except (AttributeError, OSError, PlaywrightError, TimeoutError, TypeError, ValueError):
            if self._diagnostic.root_cause == "NOT_REACHED":
                self._diagnostic = replace(self._diagnostic, root_cause="BRIDGE_EXECUTION_FAILED")
            raise SessionReadPolicyError() from None
        except BrowserPolicyError:
            raise SessionReadPolicyError() from None
        finally:
            self._permit = None
            if page is not None:
                try:
                    page.close()
                except (AttributeError, OSError, PlaywrightError, TypeError, ValueError):
                    # The context owner remains responsible for final lifecycle cleanup.
                    pass

    def _handle_request(self, route: _Route, request: _Request) -> None:
        if self._policy_failed:
            route.abort()
            return
        permit = self._permit
        if _is_canonical_ajax_endpoint(request.url, self._canonical_wwwroot):
            try:
                if permit is None:
                    raise SessionReadPolicyError()
                forwarded_headers = permit.consume(
                    request.method,
                    request.url,
                    getattr(request, "headers", None),
                )
                self._diagnostic = replace(self._diagnostic, ajax_permit_stage="REQUEST_ALLOWED")
                route.continue_(headers=forwarded_headers)
                return
            except SessionReadPolicyError:
                # An unrelated same-origin request may race the private bridge.
                # Reject it without consuming or invalidating the active permit.
                route.abort()
                return
        self._apply_general_policy(route, request)

    def _handle_response(self, response: _Response) -> None:
        """Fail closed when the one permitted AJAX request receives a redirect."""
        status = getattr(response, "status", None)
        if (
            type(status) is int
            and 300 <= status < 400
            and _is_canonical_ajax_endpoint(getattr(response, "url", None), self._canonical_wwwroot)
        ):
            self._diagnostic = replace(
                self._diagnostic,
                ajax_response_stage="TRANSPORT_FAILED",
                root_cause="OTHER_POLICY_REJECTION",
            )
            self._policy_failed = True

    def _apply_general_policy(self, route: _Route, request: _Request) -> None:
        from aaos.moodle.browser_policy import classify_browser_request

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
                self._diagnostic = replace(
                    self._diagnostic,
                    ajax_permit_stage="REQUEST_REJECTED",
                    ajax_permit_rejection_reason="OTHER_POLICY_REJECTION",
                    root_cause="OTHER_POLICY_REJECTION",
                )
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
        self._diagnostic = replace(self._diagnostic, sentinel_transition="PASS")


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


def _is_canonical_ajax_endpoint(url: object, canonical_wwwroot: str) -> bool:
    """Limit permit evaluation to the one canonical Moodle AJAX endpoint."""
    if not isinstance(url, str):
        return False
    try:
        target = urlsplit(url)
        base = urlsplit(canonical_wwwroot)
    except ValueError:
        return False
    return (
        target.scheme == base.scheme
        and target.netloc == base.netloc
        and target.path == base.path.rstrip("/") + "/lib/ajax/service.php"
        and not target.fragment
        and not target.username
        and not target.password
    )


def _extract_and_strip_provenance_header(headers: object) -> tuple[str | None, dict[str, str]]:
    """Return one matching nonce and a forwarding map with that header removed."""
    if not isinstance(headers, Mapping):
        raise SessionReadPolicyError()
    nonce_values: list[str] = []
    forwarded: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SessionReadPolicyError()
        if key.casefold() == _PROVENANCE_HEADER.casefold():
            nonce_values.append(value)
        else:
            forwarded[key] = value
    if len(nonce_values) != 1:
        return None, forwarded
    return nonce_values[0], forwarded


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
