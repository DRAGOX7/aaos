from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Never, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import urllib3.exceptions as urllib3_exceptions

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    FetchedPdf,
    IngestionErrorCode,
    RemoteFileSelection,
)

_DRIVE_FILE_ID = re.compile(
    r"drive-file:[1-9][0-9]{0,19}:"
    r"(?P<file_id>[A-Za-z0-9_-]{1,128})\Z",
    re.ASCII,
)

_DRIVE_DELIVERY_HOST = "drive.usercontent.google.com"
_INITIAL_PATH = "/uc"

_ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/octet-stream",
    }
)

_DEFAULT_TIMEOUT_SECONDS = 120.0
_MAX_REDIRECT_URL_CHARS = 8192
_READ_CHUNK_BYTES = 64 * 1024


class _HttpResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(
        self,
        amount: int | None = None,
    ) -> bytes: ...

    def close(self) -> None: ...


class _HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _HttpResponse: ...


def _fail(code: IngestionErrorCode) -> Never:
    raise DocumentIngestionFailure(code)


def _validated_drive_file_id(
    selection: RemoteFileSelection,
) -> str:
    match = _DRIVE_FILE_ID.fullmatch(selection.source_id)

    if match is None:
        _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)

    return match.group("file_id")


def _header(
    headers: Mapping[str, str],
    name: str,
) -> str | None:
    expected = name.casefold()

    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == expected and isinstance(value, str):
            return value

    return None


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None

    media_type = value.split(";", 1)[0].strip().casefold()

    return media_type or None


def _declared_size(value: str | None) -> int | None:
    if value is None:
        return None

    candidate = value.strip()

    if not candidate or len(candidate) > 20 or not candidate.isascii() or not candidate.isdecimal():
        _fail(IngestionErrorCode.NETWORK_FAILURE)

    return int(candidate)


def _validated_redirect_url(
    value: str | None,
    *,
    expected_file_id: str,
) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_REDIRECT_URL_CHARS:
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    if (
        parsed.scheme != "https"
        or parsed.hostname != _DRIVE_DELIVERY_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    if parsed.path != "/download":
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            max_num_fields=64,
        )
    except ValueError:
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    if query.get("id") != [expected_file_id]:
        _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

    return value


def _close_response(response: _HttpResponse) -> None:
    try:
        response.close()
    except (
        urllib3_exceptions.HTTPError,
        OSError,
        TypeError,
        ValueError,
    ):
        pass


def _read_bounded(
    response: _HttpResponse,
    *,
    max_bytes: int,
) -> bytes:
    body = bytearray()

    while True:
        amount = min(
            _READ_CHUNK_BYTES,
            max_bytes - len(body) + 1,
        )

        try:
            chunk = response.read(amount)
        except urllib3_exceptions.TimeoutError:
            _fail(IngestionErrorCode.NETWORK_TIMEOUT)
        except (
            urllib3_exceptions.HTTPError,
            OSError,
            TypeError,
            ValueError,
        ):
            _fail(IngestionErrorCode.NETWORK_FAILURE)

        if not isinstance(chunk, bytes):
            _fail(IngestionErrorCode.NETWORK_FAILURE)

        if not chunk:
            break

        body.extend(chunk)

        if len(body) > max_bytes:
            _fail(IngestionErrorCode.DOWNLOAD_TOO_LARGE)

    return bytes(body)


class DrivePdfFetcher:
    """Provider-scoped anonymous Google Drive PDF fetch boundary."""

    def __init__(
        self,
        *,
        http: _HttpClient,
        max_pdf_bytes: int,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(max_pdf_bytes, bool)
            or not isinstance(max_pdf_bytes, int)
            or max_pdf_bytes <= 0
        ):
            raise ValueError("max_pdf_bytes must be positive")

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")

        self._http = http
        self._max_pdf_bytes = max_pdf_bytes
        self._timeout_seconds = float(timeout_seconds)

    def supports(
        self,
        selection: RemoteFileSelection,
    ) -> bool:
        _validated_drive_file_id(selection)
        return selection.title.casefold().endswith(".pdf")

    def fetch(
        self,
        selection: RemoteFileSelection,
    ) -> FetchedPdf:
        file_id = _validated_drive_file_id(selection)

        if not selection.title.casefold().endswith(".pdf"):
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)

        query = urlencode(
            {
                "export": "download",
                "id": file_id,
            }
        )

        initial_url = f"https://{_DRIVE_DELIVERY_HOST}{_INITIAL_PATH}?{query}"

        first = self._get(initial_url)

        try:
            if first.status != 303:
                _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

            redirect_url = _validated_redirect_url(
                _header(first.headers, "location"),
                expected_file_id=file_id,
            )
        finally:
            _close_response(first)

        final = self._get(redirect_url)

        try:
            if 300 <= final.status < 400:
                _fail(IngestionErrorCode.UNAUTHORIZED_REDIRECT)

            if final.status != 200:
                _fail(IngestionErrorCode.NETWORK_FAILURE)

            media_type = _media_type(
                _header(
                    final.headers,
                    "content-type",
                )
            )

            if media_type not in _ALLOWED_MEDIA_TYPES:
                _fail(IngestionErrorCode.INVALID_PDF)

            declared_size = _declared_size(
                _header(
                    final.headers,
                    "content-length",
                )
            )

            if declared_size is not None and declared_size > self._max_pdf_bytes:
                _fail(IngestionErrorCode.DOWNLOAD_TOO_LARGE)

            body = _read_bounded(
                final,
                max_bytes=self._max_pdf_bytes,
            )

            if not body.startswith(b"%PDF-"):
                _fail(IngestionErrorCode.INVALID_PDF)

            return FetchedPdf(
                body=body,
                source_filename=selection.title,
                advisory_media_type=media_type,
            )
        finally:
            _close_response(final)

    def _get(
        self,
        url: str,
    ) -> _HttpResponse:
        try:
            return self._http.request(
                "GET",
                url,
                redirect=False,
                retries=False,
                preload_content=False,
                timeout=self._timeout_seconds,
            )
        except urllib3_exceptions.TimeoutError:
            _fail(IngestionErrorCode.NETWORK_TIMEOUT)
        except (
            urllib3_exceptions.HTTPError,
            OSError,
            TypeError,
            ValueError,
        ):
            _fail(IngestionErrorCode.NETWORK_FAILURE)
