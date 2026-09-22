"""Strict parsing for Google Drive folders explicitly embedded by Moodle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from aaos.academic.source import RawRemoteFile
from aaos.moodle.browser_models import BrowserPolicyError

_DRIVE_HOST = "drive.google.com"
_DRIVE_FOLDER_PATH = "/embeddedfolderview"
_DRIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DRIVE_FILE_PATH_PATTERN = re.compile(r"^/file/d/([A-Za-z0-9_-]{1,128})/view$")
_FILE_LIKE_TITLE_PATTERN = re.compile(r"\.[A-Za-z0-9]{1,12}$")

_MAX_TITLE_CHARACTERS = 512
_MAX_EMBEDDED_FOLDERS = 32
_MAX_FRAME_LINKS = 2_000


@dataclass(frozen=True, slots=True)
class DriveFolderReference:
    """One validated Google Drive folder explicitly embedded by Moodle."""

    folder_id: str


class _MarkupCollector(HTMLParser):
    """Collect only iframe sources and anchor labels needed by this parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.iframes: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        lowered = tag.lower()

        if lowered == "iframe":
            src = values.get("src")
            if isinstance(src, str):
                self.iframes.append(src)

        if lowered == "a":
            href = values.get("href")
            if isinstance(href, str):
                self._anchor_href = href
                self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor_href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor_href is None:
            return

        self.links.append(
            (
                self._anchor_href,
                " ".join(" ".join(self._anchor_text).split()),
            )
        )
        self._anchor_href = None
        self._anchor_text = []


def _parse_markup(html: str) -> _MarkupCollector:
    if not isinstance(html, str):
        raise BrowserPolicyError()

    collector = _MarkupCollector()

    try:
        collector.feed(html)
        collector.close()
    except (TypeError, ValueError):
        raise BrowserPolicyError() from None

    return collector


def _validated_url_parts(value: str) -> tuple[str, str, str, dict[str, list[str]], str]:
    try:
        parsed = urlsplit(value)
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=False,
        )
    except (TypeError, ValueError):
        raise BrowserPolicyError() from None

    try:
        port = parsed.port
    except ValueError:
        raise BrowserPolicyError() from None

    if (
        parsed.scheme != "https"
        or parsed.hostname != _DRIVE_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BrowserPolicyError()

    return (
        parsed.scheme,
        parsed.hostname,
        parsed.path,
        query,
        parsed.fragment,
    )


def _validated_drive_id(value: str) -> str:
    if not _DRIVE_ID_PATTERN.fullmatch(value):
        raise BrowserPolicyError()
    return value


def _looks_like_file_title(value: str) -> bool:
    return bool(_FILE_LIKE_TITLE_PATTERN.search(" ".join(value.split())))


def _validated_title(value: str) -> str:
    title = " ".join(value.split())

    if (
        not title
        or len(title) > _MAX_TITLE_CHARACTERS
        or "/" in title
        or "\\" in title
        or any(ord(character) < 32 or ord(character) == 127 for character in title)
    ):
        raise BrowserPolicyError()

    return title


def _validated_course_source_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 20 or not value.isdigit():
        raise BrowserPolicyError()

    return value


def _parse_folder_reference(src: str) -> DriveFolderReference | None:
    try:
        parsed = urlsplit(src)
    except (TypeError, ValueError):
        raise BrowserPolicyError() from None

    # Ignore unrelated HTTPS embeds. They remain outside this capability.
    if (
        parsed.scheme == "https"
        and parsed.hostname != _DRIVE_HOST
        and parsed.path != _DRIVE_FOLDER_PATH
    ):
        return None

    # Anything claiming to be a Drive folder embed must match the exact
    # provider-owned shape.
    if parsed.hostname == _DRIVE_HOST or parsed.path == _DRIVE_FOLDER_PATH:
        _scheme, _host, path, query, fragment = _validated_url_parts(src)

        if (
            path != _DRIVE_FOLDER_PATH
            or set(query) != {"id"}
            or len(query["id"]) != 1
            or fragment not in {"", "list", "grid"}
        ):
            raise BrowserPolicyError()

        return DriveFolderReference(_validated_drive_id(query["id"][0]))

    # Active/non-HTTPS iframe schemes are never acceptable input to this
    # provider-scoped discovery surface.
    if parsed.scheme not in {"", "https"}:
        raise BrowserPolicyError()

    return None


def parse_embedded_drive_folders(
    html: str,
) -> tuple[DriveFolderReference, ...]:
    """Parse only approved Google Drive folder embeds from Moodle markup."""

    markup = _parse_markup(html)
    folders: list[DriveFolderReference] = []
    seen: set[str] = set()

    for src in markup.iframes:
        folder = _parse_folder_reference(src)

        if folder is None:
            continue

        if folder.folder_id in seen:
            continue

        seen.add(folder.folder_id)
        folders.append(folder)

        if len(folders) > _MAX_EMBEDDED_FOLDERS:
            raise BrowserPolicyError()

    return tuple(folders)


def _drive_file_id(href: str) -> str | None:
    try:
        parsed = urlsplit(href)
    except (TypeError, ValueError):
        raise BrowserPolicyError() from None

    # Non-Drive links are not files owned by this provider capability.
    if parsed.hostname != _DRIVE_HOST:
        return None

    _scheme, _host, path, query, fragment = _validated_url_parts(href)

    match = _DRIVE_FILE_PATH_PATTERN.fullmatch(path)

    if match is None:
        return None

    if fragment:
        raise BrowserPolicyError()

    # Live Drive currently adds `usp`; no other query semantics are needed
    # for stable file identity.
    if not set(query).issubset({"usp"}):
        raise BrowserPolicyError()

    if "usp" in query and len(query["usp"]) != 1:
        raise BrowserPolicyError()

    return _validated_drive_id(match.group(1))


def parse_embedded_drive_files(
    html: str,
    *,
    course_source_id: str,
) -> tuple[RawRemoteFile, ...]:
    """Map top-level files from one approved Drive frame into metadata."""

    course_id = _validated_course_source_id(course_source_id)
    markup = _parse_markup(html)

    if len(markup.links) > _MAX_FRAME_LINKS:
        raise BrowserPolicyError()

    files: list[RawRemoteFile] = []
    seen: dict[str, str] = {}

    for href, raw_title in markup.links:
        title = " ".join(raw_title.split())
        file_id = _drive_file_id(href)

        if file_id is None:
            # A file-looking row whose target escapes the approved Drive
            # file shape is ambiguous and must fail closed.
            if _looks_like_file_title(title):
                raise BrowserPolicyError()
            continue

        validated_title = _validated_title(title)

        previous_title = seen.get(file_id)

        if previous_title is not None:
            if previous_title != validated_title:
                raise BrowserPolicyError()
            continue

        seen[file_id] = validated_title

        files.append(
            RawRemoteFile(
                source_id=f"drive-file:{course_id}:{file_id}",
                course_source_id=course_id,
                title=validated_title,
                remote_url=None,
            )
        )

    return tuple(files)
