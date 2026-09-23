from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from aaos.documents.contracts import (
    FetchedDocx,
    FetchedPdf,
    RemoteFileSelection,
)
from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher
from aaos.moodle.file_download import (
    BrowserAuthenticatedFetcher,
    MoodleFileAuthorizer,
)

SOURCE = "moodle:fixture"
WWWROOT = "https://moodle.example/current"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


def selection(
    *,
    source_id: str = "resource-cm:42",
    title: str = "Lecture",
) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=11,
        course_id=3,
        source_system=SOURCE,
        source_id=source_id,
        title=title,
    )


@dataclass
class FakeResponse:
    status: int
    headers: dict[str, str]
    payload: bytes

    def body(self) -> bytes:
        return self.payload


class FakeRequest:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[
            tuple[str, dict[str, object]]
        ] = []

    def get(
        self,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.calls.append((url, dict(kwargs)))
        return self.response


class FakeContext:
    def __init__(self, response: FakeResponse) -> None:
        self.request = FakeRequest(response)


def moodle_fetch(
    response: FakeResponse,
) -> FetchedPdf | FetchedDocx:
    context = FakeContext(response)
    fetcher = BrowserAuthenticatedFetcher(
        context=cast(Any, context),
        authorizer=MoodleFileAuthorizer(
            canonical_wwwroot=WWWROOT,
            expected_source_system=SOURCE,
        ),
    )
    return fetcher.fetch(selection())


def test_moodle_resource_emits_fetched_docx_for_docx_response() -> None:
    result = moodle_fetch(
        FakeResponse(
            status=200,
            headers={
                "content-type": DOCX_MIME,
                "content-disposition": (
                    'attachment; filename="lecture.docx"'
                ),
            },
            payload=b"PK-docx-fixture",
        )
    )

    assert isinstance(result, FetchedDocx)
    assert result.body == b"PK-docx-fixture"
    assert result.source_filename == "lecture.docx"
    assert result.advisory_media_type == DOCX_MIME


def test_moodle_pdf_response_remains_fetched_pdf() -> None:
    result = moodle_fetch(
        FakeResponse(
            status=200,
            headers={
                "content-type": "application/pdf",
                "content-disposition": (
                    'attachment; filename="lecture.pdf"'
                ),
            },
            payload=b"%PDF-1.7",
        )
    )

    assert isinstance(result, FetchedPdf)
    assert result.source_filename == "lecture.pdf"


class MoodleDocxFetcher:
    def supports(
        self,
        item: RemoteFileSelection,
    ) -> bool:
        return item.source_id == "resource-cm:42"

    def fetch(
        self,
        item: RemoteFileSelection,
    ) -> FetchedDocx:
        assert item.source_id == "resource-cm:42"
        return FetchedDocx(
            body=b"docx",
            source_filename="lecture.docx",
            advisory_media_type=DOCX_MIME,
        )


class DrivePdfFetcher:
    def supports(
        self,
        item: RemoteFileSelection,
    ) -> bool:
        return item.title.casefold().endswith(".pdf")

    def fetch(
        self,
        item: RemoteFileSelection,
    ) -> FetchedPdf:
        return FetchedPdf(
            body=b"%PDF",
            source_filename=item.title,
            advisory_media_type="application/pdf",
        )


def test_router_preserves_moodle_docx_payload() -> None:
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=cast(Any, MoodleDocxFetcher()),
        drive_fetcher=cast(Any, DrivePdfFetcher()),
    )

    result = router.fetch(selection())

    assert isinstance(result, FetchedDocx)
    assert result.source_filename == "lecture.docx"


def test_drive_docx_remains_unsupported() -> None:
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=cast(Any, MoodleDocxFetcher()),
        drive_fetcher=cast(Any, DrivePdfFetcher()),
    )

    drive_docx = selection(
        source_id="drive-file:42:AbCdEf123",
        title="lecture.docx",
    )

    assert router.supports(drive_docx) is False
