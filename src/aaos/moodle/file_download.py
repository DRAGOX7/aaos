"""Phase-3-only authorization and authenticated fetching of one Moodle Resource."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePath
from typing import Any, NoReturn, cast
from urllib.parse import unquote, urljoin, urlsplit

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from aaos.documents.contracts import (
    FETCH_TIMEOUT_MS,
    MAX_AUTHORIZED_REDIRECTS,
    MAX_PDF_BYTES,
    DocumentIngestionFailure,
    FetchedPdf,
    IngestionErrorCode,
    RemoteFileSelection,
)
from aaos.moodle.browser_models import (
    BrowserPolicyError,
    BrowserProfileBusyError,
    acquire_browser_profile_lock,
    resolve_browser_profile_paths,
    validate_browser_profile_binding,
)
from aaos.moodle.browser_runtime import launch_msedge_persistent_context
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.session_lifetime import wait_for_authenticated_course_index

_SOURCE_ID = re.compile(r"resource-cm:([1-9][0-9]*)\Z", re.ASCII)
_CONTENT_DISPOSITION_FILENAME = re.compile(
    r'(?:^|;)\s*filename\s*=\s*(?:"([^"\r\n]*)"|([^;\s\r\n]+))\s*(?:;|$)',
    re.IGNORECASE,
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AUTH_TIMEOUT_SECONDS = 600
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(frozen=True, slots=True)
class ResourceFetchPermit:
    """A redacted capability for one selected Moodle Resource."""

    remote_file_id: int
    source_id: str
    initial_url: str = field(repr=False)


class MoodleFileAuthorizer:
    """Authorize only a selected Resource and its Moodle-owned file continuation."""

    def __init__(self, *, canonical_wwwroot: str, expected_source_system: str) -> None:
        scheme, netloc, host, base_path = _validated_wwwroot(canonical_wwwroot)
        if not _safe_text(expected_source_system):
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        self._wwwroot = canonical_wwwroot.rstrip("/")
        self._scheme = scheme
        self._netloc = netloc
        self._host = host
        self._base_path = base_path
        self._expected_source_system = expected_source_system

    def authorize(self, selection: RemoteFileSelection) -> ResourceFetchPermit:
        match = _SOURCE_ID.fullmatch(selection.source_id)
        if (
            selection.source_system != self._expected_source_system
            or not _positive_int(selection.remote_file_id)
            or not _positive_int(selection.course_id)
        ):
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        if match is None:
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        cmid = match.group(1)
        return ResourceFetchPermit(
            remote_file_id=selection.remote_file_id,
            source_id=selection.source_id,
            initial_url=f"{self._wwwroot}/mod/resource/view.php?id={cmid}",
        )

    def authorize_redirect(
        self, *, permit: ResourceFetchPermit, current_url: str, location: str
    ) -> str:
        if (
            not isinstance(location, str)
            or not location
            or not self._is_chain_url(permit, current_url)
        ):
            _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
        target = urljoin(current_url, location)
        if not self._is_pluginfile_url(target):
            _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
        return target

    def authorize_final_url(self, *, permit: ResourceFetchPermit, url: str) -> None:
        if url == permit.initial_url:
            return
        if not self._is_pluginfile_url(url):
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)

    def _is_chain_url(self, permit: ResourceFetchPermit, url: object) -> bool:
        return url == permit.initial_url or self._is_pluginfile_url(url)

    def _is_pluginfile_url(self, url: object) -> bool:
        parsed = self._split_confined(url)
        if parsed is None:
            return False
        relative_path = parsed.path[len(self._base_path) :]
        parts = relative_path.split("/")
        return (
            len(parts) >= 7
            and parts[0] == ""
            and parts[1] == "pluginfile.php"
            and _positive_decimal(parts[2])
            and parts[3] == "mod_resource"
            and parts[4] == "content"
            and _positive_decimal(parts[5])
            and all(_safe_path_segment(part) for part in parts[6:])
        )

    def _split_confined(self, url: object) -> Any | None:
        if not isinstance(url, str):
            return None
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != self._scheme
            or parsed.netloc != self._netloc
            or parsed.hostname != self._host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port is None
            and ":" in parsed.netloc.rsplit("@", 1)[-1]
            or not _beneath_base(parsed.path, self._base_path)
        ):
            return None
        return parsed


class _ResourceHrefParser(HTMLParser):
    """Collect anchor hrefs and no other HTML content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_markup = False
        self.hrefs: list[str] = []

    def handle_decl(self, _decl: str) -> None:
        self.saw_markup = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.saw_markup = True
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                self.hrefs.append(value)
                break


def _parse_resource_html(body: bytes, headers: object) -> tuple[bool, tuple[str, ...]]:
    """Classify a non-PDF body as HTML and retain only anchor references."""
    if body.startswith(b"%PDF-"):
        return False, ()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    parser = _ResourceHrefParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError):
        parser.hrefs.clear()
    media_type = _media_type(_header(headers, "content-type"))
    is_html = media_type in _HTML_MEDIA_TYPES or parser.saw_markup
    return is_html, tuple(parser.hrefs)


class BrowserAuthenticatedFetcher:
    """Fetch one authorized Resource through an existing authenticated browser context."""

    def __init__(
        self,
        *,
        context: BrowserContext,
        authorizer: MoodleFileAuthorizer,
        timeout_ms: int = FETCH_TIMEOUT_MS,
    ) -> None:
        if not _positive_int(timeout_ms):
            raise ValueError("fetch timeout must be positive")
        self._context = context
        self._authorizer = authorizer
        self._timeout_ms = timeout_ms

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf:
        permit = self._authorizer.authorize(selection)
        current_url = permit.initial_url
        visited = {current_url}
        redirects = 0
        while True:
            response = self._get(current_url)
            status = response.status
            if status in _REDIRECT_STATUSES:
                redirects += 1
                if redirects > MAX_AUTHORIZED_REDIRECTS:
                    _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
                location = _header(response.headers, "location")
                target = self._authorizer.authorize_redirect(
                    permit=permit,
                    current_url=current_url,
                    location=location or "",
                )
                if target in visited:
                    _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
                visited.add(target)
                current_url = target
                continue
            if status != 200:
                _fail(IngestionErrorCode.NETWORK_FAILURE)
            self._authorizer.authorize_final_url(permit=permit, url=current_url)
            declared_size = _declared_size(_header(response.headers, "content-length"))
            if declared_size is not None and declared_size > MAX_PDF_BYTES:
                _fail(IngestionErrorCode.DOWNLOAD_TOO_LARGE)
            try:
                body = response.body()
            except PlaywrightTimeoutError:
                _fail(IngestionErrorCode.NETWORK_TIMEOUT)
            except (PlaywrightError, OSError, TypeError, ValueError):
                _fail(IngestionErrorCode.NETWORK_FAILURE)
            if len(body) > MAX_PDF_BYTES:
                _fail(IngestionErrorCode.DOWNLOAD_TOO_LARGE)
            is_html, hrefs = _parse_resource_html(body, response.headers)
            if current_url == permit.initial_url and is_html:
                candidates: set[str] = set()
                for href in hrefs:
                    try:
                        candidates.add(
                            self._authorizer.authorize_redirect(
                                permit=permit,
                                current_url=permit.initial_url,
                                location=href,
                            )
                        )
                    except DocumentIngestionFailure:
                        continue
                if len(candidates) != 1:
                    _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
                continuation = next(iter(candidates))
                if continuation in visited:
                    _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
                visited.add(continuation)
                current_url = continuation
                continue
            if is_html:
                _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)
            cmid = permit.source_id.removeprefix("resource-cm:")
            return FetchedPdf(
                body=body,
                source_filename=_source_filename(
                    _header(response.headers, "content-disposition"), cmid
                ),
                advisory_media_type=_media_type(_header(response.headers, "content-type")),
            )

    def _get(self, url: str) -> Any:
        try:
            return self._context.request.get(
                url,
                max_redirects=0,
                timeout=self._timeout_ms,
            )
        except PlaywrightTimeoutError:
            _fail(IngestionErrorCode.NETWORK_TIMEOUT)
        except (PlaywrightError, OSError, TypeError, ValueError):
            _fail(IngestionErrorCode.NETWORK_FAILURE)


@contextmanager
def open_phase3_fetcher_session(
    *,
    canonical_wwwroot: str,
    expected_source_system: str,
    settings: MoodleSettings,
    auth_paths: AuthPaths,
) -> Iterator[BrowserAuthenticatedFetcher]:
    """Open one existing-profile Edge lifetime and yield its Phase-3-only fetcher."""
    authorizer = MoodleFileAuthorizer(
        canonical_wwwroot=canonical_wwwroot,
        expected_source_system=expected_source_system,
    )
    parsed = urlsplit(canonical_wwwroot)
    canonical_origin = f"{parsed.scheme}://{parsed.netloc}"
    if (
        settings.mode != "browser"
        or settings.source_system != expected_source_system
        or settings.base_origin != canonical_origin
        or canonical_wwwroot != settings.base_origin + "/current"
    ):
        _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
    try:
        profile = resolve_browser_profile_paths(auth_paths, settings.base_origin)
        lease = acquire_browser_profile_lock(profile)
    except (BrowserPolicyError, BrowserProfileBusyError, OSError, TypeError, ValueError):
        _fail(IngestionErrorCode.NETWORK_FAILURE)
    with lease:
        try:
            validate_browser_profile_binding(
                profile,
                expected_origin=settings.base_origin,
                expected_alias=settings.auth_alias,
            )
            context = launch_msedge_persistent_context(
                channel="msedge",
                headless=False,
                user_data_dir=profile.profile_dir,
                accept_downloads=False,
                service_workers="block",
            )
        except (BrowserPolicyError, OSError, TimeoutError, TypeError, ValueError):
            _fail(IngestionErrorCode.NETWORK_FAILURE)
        try:
            page = cast(Any, context).new_page()
            page.goto(
                canonical_wwwroot + "/login/index.php",
                wait_until="domcontentloaded",
                timeout=_AUTH_TIMEOUT_SECONDS * 1000,
            )
            if not wait_for_authenticated_course_index(
                page,
                course_index_url=canonical_wwwroot + "/my/courses.php",
                timeout_seconds=_AUTH_TIMEOUT_SECONDS,
            ):
                _fail(IngestionErrorCode.NETWORK_FAILURE)
            page.close()
            yield BrowserAuthenticatedFetcher(
                context=cast(BrowserContext, context), authorizer=authorizer
            )
        except DocumentIngestionFailure:
            raise
        except (PlaywrightTimeoutError, TimeoutError):
            _fail(IngestionErrorCode.NETWORK_TIMEOUT)
        except (
            AttributeError,
            BrowserPolicyError,
            OSError,
            PlaywrightError,
            TypeError,
            ValueError,
        ):
            _fail(IngestionErrorCode.NETWORK_FAILURE)
        finally:
            try:
                context.close()
            except (AttributeError, OSError, PlaywrightError, TypeError, ValueError):
                _fail(IngestionErrorCode.NETWORK_FAILURE)


def _validated_wwwroot(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, str) or value != value.rstrip("/"):
        _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
    try:
        parsed = urlsplit(value)
        port = parsed.port
        ip = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        ip = None
        try:
            parsed = urlsplit(value if isinstance(value, str) else "")
            port = parsed.port
        except ValueError:
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or parsed.path == "/"
        or "//" in parsed.path
        or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
        or port is None
        and ":" in parsed.netloc.rsplit("@", 1)[-1]
        or host == "localhost"
        or host.endswith(".localhost")
        or ip is not None
        and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        )
    ):
        _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
    return parsed.scheme, parsed.netloc, host, parsed.path


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _positive_decimal(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]*", value, re.ASCII))


def _safe_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _safe_path_segment(value: str) -> bool:
    if not value or re.fullmatch(r"(?:[^%]|%[0-9A-Fa-f]{2})*", value) is None:
        return False
    decoded = unquote(value)
    return (
        bool(decoded)
        and decoded not in {".", ".."}
        and "/" not in decoded
        and "\\" not in decoded
        and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    )


def _beneath_base(path: str, base_path: str) -> bool:
    return path == base_path or path.startswith(base_path + "/")


def _header(headers: object, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == name and isinstance(value, str):
            return value
    return None


def _declared_size(value: str | None) -> int | None:
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    return int(value)


def _source_filename(content_disposition: str | None, cmid: str) -> str:
    fallback = f"resource-{cmid}.pdf"
    if content_disposition is None:
        return fallback
    match = _CONTENT_DISPOSITION_FILENAME.search(content_disposition)
    if match is None:
        return fallback
    candidate = (match.group(1) or match.group(2) or "").strip()
    if (
        not candidate
        or len(candidate) > 255
        or candidate in {".", ".."}
        or PurePath(candidate).name != candidate
        or "/" in candidate
        or "\\" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        return fallback
    return candidate


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", maxsplit=1)[0].strip().lower()
    return media_type or None


def _fail(code: IngestionErrorCode) -> NoReturn:
    raise DocumentIngestionFailure(code)
