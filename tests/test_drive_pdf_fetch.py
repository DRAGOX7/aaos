from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import pytest

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    IngestionErrorCode,
    RemoteFileSelection,
)
from aaos.documents.drive_pdf_fetch import DrivePdfFetcher, _HttpResponse

DRIVE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
SOURCE_ID = f"drive-file:2037:{DRIVE_ID}"


def selection(
    *,
    source_id: str = SOURCE_ID,
    title: str = "Study notes.pdf",
) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=17,
        course_id=5,
        source_system="moodle:just:summer-2026:bootstrap",
        source_id=source_id,
        title=title,
    )


@dataclass
class FakeResponse:
    status: int
    headers: Mapping[str, str]
    chunks: list[bytes] = field(default_factory=list)
    closed: bool = False

    def read(self, amount: int | None = None) -> bytes:
        if not self.chunks:
            return b""

        chunk = self.chunks.pop(0)

        if amount is None or len(chunk) <= amount:
            return chunk

        self.chunks.insert(0, chunk[amount:])
        return chunk[:amount]

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeHttp:
    responses: list[FakeResponse]
    calls: list[dict[str, object]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                **kwargs,
            }
        )

        if not self.responses:
            raise AssertionError("unexpected HTTP request")

        return self.responses.pop(0)


def redirect_location(
    *,
    host: str = "drive.usercontent.google.com",
    drive_id: str = DRIVE_ID,
) -> str:
    return f"https://{host}/download?id={drive_id}&export=download&provider_token=opaque"


def success_http(
    body: bytes = b"%PDF-1.7\nsynthetic-pdf",
) -> FakeHttp:
    return FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                    "Content-Length": "0",
                },
            ),
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(body)),
                },
                chunks=[body],
            ),
        ]
    )


def assert_failure(
    caught: pytest.ExceptionInfo[DocumentIngestionFailure],
    code: IngestionErrorCode,
) -> None:
    assert caught.value.code is code
    assert DRIVE_ID not in repr(caught.value)


def test_supports_only_valid_drive_pdf_identity() -> None:
    fetcher = DrivePdfFetcher(
        http=FakeHttp([]),
        max_pdf_bytes=1024,
    )

    assert fetcher.supports(selection()) is True
    assert fetcher.supports(selection(title="Study notes.docx")) is False

    with pytest.raises(DocumentIngestionFailure) as caught:
        fetcher.supports(selection(source_id="drive-file:2037:"))

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_ROUTE,
    )


def test_fetch_uses_only_source_identity_for_initial_drive_request() -> None:
    http = success_http()
    fetcher = DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    )

    result = fetcher.fetch(selection())

    assert len(http.calls) == 2

    initial = http.calls[0]
    parsed = urlsplit(str(initial["url"]))

    assert initial["method"] == "GET"
    assert parsed.scheme == "https"
    assert parsed.hostname == "drive.usercontent.google.com"
    assert parsed.path == "/uc"
    assert parsed.fragment == ""
    assert parsed.username is None
    assert parsed.password is None

    query = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    assert query == {
        "export": ["download"],
        "id": [DRIVE_ID],
    }

    assert initial["redirect"] is False
    assert initial["retries"] is False
    assert initial["preload_content"] is False

    assert result.body.startswith(b"%PDF-")
    assert result.source_filename == "Study notes.pdf"
    assert result.advisory_media_type == "application/octet-stream"


def test_fetch_requires_exactly_one_303_redirect() -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": "12",
                },
                chunks=[b"%PDF-direct"],
            )
        ]
    )

    fetcher = DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        fetcher.fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
    )
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (
            f"http://drive.usercontent.google.com/download?id={DRIVE_ID}",
            IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        ),
        (
            f"https://evil.example/download?id={DRIVE_ID}",
            IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        ),
        (
            "https://drive.usercontent.google.com/download?id=different-file",
            IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        ),
        (
            f"https://user:password@drive.usercontent.google.com/download?id={DRIVE_ID}",
            IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        ),
        (
            f"https://drive.usercontent.google.com/download?id={DRIVE_ID}#fragment",
            IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        ),
    ],
)
def test_fetch_rejects_unconfined_redirect(
    location: str,
    expected: IngestionErrorCode,
) -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={"Location": location},
            )
        ]
    )

    fetcher = DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        fetcher.fetch(selection())

    assert_failure(caught, expected)
    assert len(http.calls) == 1


def test_fetch_rejects_second_redirect() -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
        ]
    )

    fetcher = DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        fetcher.fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
    )
    assert len(http.calls) == 2


@pytest.mark.parametrize(
    "media_type",
    [
        "application/pdf",
        "application/octet-stream",
    ],
)
def test_fetch_accepts_pdf_magic_with_proven_media_types(
    media_type: str,
) -> None:
    body = b"%PDF-1.7\nvalid"

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": media_type,
                    "Content-Length": str(len(body)),
                },
                chunks=[body],
            ),
        ]
    )

    result = DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    ).fetch(selection())

    assert result.body == body
    assert result.advisory_media_type == media_type


def test_fetch_rejects_non_pdf_body_even_with_pdf_media_type() -> None:
    body = b"<html>not a pdf</html>"

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": str(len(body)),
                },
                chunks=[body],
            ),
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.INVALID_PDF,
    )


def test_fetch_rejects_declared_oversize_before_body_read() -> None:
    final = FakeResponse(
        status=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": "1025",
        },
        chunks=[b"%PDF-should-not-be-read"],
    )

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            final,
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.DOWNLOAD_TOO_LARGE,
    )

    assert final.chunks == [b"%PDF-should-not-be-read"]


def test_fetch_rejects_streamed_body_that_crosses_size_limit() -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": "application/octet-stream",
                },
                chunks=[
                    b"%PDF-123",
                    b"456789",
                ],
            ),
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=10,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.DOWNLOAD_TOO_LARGE,
    )


def test_fetch_closes_both_responses() -> None:
    first = FakeResponse(
        status=303,
        headers={
            "Location": redirect_location(),
        },
    )

    body = b"%PDF-1.7\nvalid"

    second = FakeResponse(
        status=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)),
        },
        chunks=[body],
    )

    DrivePdfFetcher(
        http=FakeHttp([first, second]),
        max_pdf_bytes=1024,
    ).fetch(selection())

    assert first.closed is True
    assert second.closed is True


def test_fetch_rejects_non_download_redirect_path() -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": (f"https://drive.usercontent.google.com/not-download?id={DRIVE_ID}")
                },
            )
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
    )
    assert len(http.calls) == 1


def test_fetch_rejects_duplicate_redirect_id() -> None:
    location = f"https://drive.usercontent.google.com/download?id={DRIVE_ID}&id={DRIVE_ID}"

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={"Location": location},
            )
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
    )


def test_fetch_rejects_final_non_200_status() -> None:
    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=404,
                headers={},
            ),
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.NETWORK_FAILURE,
    )


def test_fetch_rejects_unsupported_final_media_type() -> None:
    body = b"%PDF-1.7\nvalid"

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={
                    "Location": redirect_location(),
                },
            ),
            FakeResponse(
                status=200,
                headers={
                    "Content-Type": "text/plain",
                    "Content-Length": str(len(body)),
                },
                chunks=[body],
            ),
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.INVALID_PDF,
    )


def test_fetch_rejects_direct_non_pdf_selection() -> None:
    http = FakeHttp([])

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(
            selection(
                title="Study notes.docx",
            )
        )

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_ROUTE,
    )

    assert http.calls == []


def test_fetch_sends_no_authentication_or_cookie_headers() -> None:
    http = success_http()

    DrivePdfFetcher(
        http=http,
        max_pdf_bytes=1024,
    ).fetch(selection())

    assert len(http.calls) == 2

    for call in http.calls:
        assert "headers" not in call


def test_fetch_maps_timeout_to_network_timeout() -> None:
    import urllib3.exceptions

    class TimeoutHttp:
        def request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> _HttpResponse:
            del method, url, kwargs
            raise urllib3.exceptions.TimeoutError("synthetic timeout")

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=TimeoutHttp(),
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.NETWORK_TIMEOUT,
    )


def test_fetch_maps_http_failure_to_network_failure() -> None:
    import urllib3.exceptions

    class BrokenHttp:
        def request(
            self,
            method: str,
            url: str,
            **kwargs: object,
        ) -> _HttpResponse:
            del method, url, kwargs
            raise urllib3.exceptions.HTTPError("synthetic network failure")

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=BrokenHttp(),
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.NETWORK_FAILURE,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/download/child",
        "/DOWNLOAD",
    ],
)
def test_fetch_rejects_non_exact_download_redirect_path(
    path: str,
) -> None:
    location = f"https://drive.usercontent.google.com{path}?id={DRIVE_ID}"

    http = FakeHttp(
        responses=[
            FakeResponse(
                status=303,
                headers={"Location": location},
            )
        ]
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        DrivePdfFetcher(
            http=http,
            max_pdf_bytes=1024,
        ).fetch(selection())

    assert_failure(
        caught,
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
    )

    assert len(http.calls) == 1
