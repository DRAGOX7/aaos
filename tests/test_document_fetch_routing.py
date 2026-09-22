from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    FetchedPdf,
    IngestionErrorCode,
    RemoteFileSelection,
)

SOURCE = "moodle:just:summer-2026:bootstrap"


def selection(
    source_id: str,
    title: str,
    *,
    remote_file_id: int = 1,
) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=remote_file_id,
        course_id=2,
        source_system=SOURCE,
        source_id=source_id,
        title=title,
    )


@dataclass
class RecordingFetcher:
    supported: bool
    calls: list[str] = field(default_factory=list)

    def supports(self, item: RemoteFileSelection) -> bool:
        return self.supported

    def fetch(self, item: RemoteFileSelection) -> FetchedPdf:
        self.calls.append(item.source_id)
        return FetchedPdf(
            body=b"%PDF-test",
            source_filename=item.title,
            advisory_media_type="application/pdf",
        )


def test_router_sends_native_moodle_resource_only_to_moodle_fetcher() -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    moodle = RecordingFetcher(True)
    drive = RecordingFetcher(True)
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=moodle,
        drive_fetcher=drive,
    )

    item = selection("resource-cm:4448", "Course syllabus.pdf")

    assert router.supports(item) is True
    router.fetch(item)

    assert moodle.calls == ["resource-cm:4448"]
    assert drive.calls == []


def test_router_sends_drive_pdf_only_to_drive_fetcher() -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    moodle = RecordingFetcher(True)
    drive = RecordingFetcher(True)
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=moodle,
        drive_fetcher=drive,
    )

    item = selection(
        "drive-file:2037:drive-pdf-id",
        "A STAI 301 Study notes.pdf",
    )

    assert router.supports(item) is True
    router.fetch(item)

    assert moodle.calls == []
    assert drive.calls == ["drive-file:2037:drive-pdf-id"]


@pytest.mark.parametrize(
    ("title", "extension"),
    [
        ("notes.docx", ".docx"),
        ("slides.pptx", ".pptx"),
        ("notebook.ipynb", ".ipynb"),
    ],
)
def test_router_marks_non_pdf_drive_material_unsupported(
    title: str,
    extension: str,
) -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    moodle = RecordingFetcher(True)
    drive = RecordingFetcher(True)
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=moodle,
        drive_fetcher=drive,
    )

    item = selection(
        "drive-file:2037:provider-id",
        title,
    )

    assert title.endswith(extension)
    assert router.supports(item) is False
    assert moodle.calls == []
    assert drive.calls == []


def test_router_rejects_unknown_source_identity() -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=RecordingFetcher(True),
        drive_fetcher=RecordingFetcher(True),
    )

    item = selection(
        "https://example.com/file.pdf",
        "file.pdf",
    )

    assert router.supports(item) is False


def test_router_fetch_rejects_unsupported_identity_without_delegation() -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    moodle = RecordingFetcher(True)
    drive = RecordingFetcher(True)
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=moodle,
        drive_fetcher=drive,
    )

    item = selection(
        "drive-file:2037:provider-id",
        "notebook.ipynb",
    )

    with pytest.raises(DocumentIngestionFailure) as caught:
        router.fetch(item)

    assert caught.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE
    assert moodle.calls == []
    assert drive.calls == []


def test_router_fetch_rejects_provider_that_declines_selection() -> None:
    from aaos.documents.fetch_routing import ProviderRoutingPdfFetcher

    moodle = RecordingFetcher(True)
    drive = RecordingFetcher(False)
    router = ProviderRoutingPdfFetcher(
        moodle_fetcher=moodle,
        drive_fetcher=drive,
    )

    item = selection(
        "drive-file:2037:provider-id",
        "material.pdf",
    )

    assert router.supports(item) is False

    with pytest.raises(DocumentIngestionFailure) as caught:
        router.fetch(item)

    assert caught.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE
    assert moodle.calls == []
    assert drive.calls == []
