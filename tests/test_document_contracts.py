"""Tests for the frozen Phase 3 document-ingestion contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pypdfium2.version import PYPDFIUM_INFO  # type: ignore[import-untyped]

from aaos.documents.contracts import (
    FETCH_TIMEOUT_MS,
    FILE_TYPE,
    MAX_AUTHORIZED_REDIRECTS,
    MAX_PDF_BYTES,
    MAX_TRANSIENT_RETRIES,
    BatchIngestionResult,
    BlobInstall,
    DocumentIngestionFailure,
    DocumentIngestionResult,
    DocumentResultCode,
    ExtractedPage,
    ExtractionStatus,
    FetchedPdf,
    FileType,
    IngestionErrorCode,
    IngestionMode,
    IngestionStatus,
    ParsedPdf,
    RemoteFileSelection,
    TextSource,
)


def test_pypdfium2_runtime_version_matches_lock() -> None:
    assert PYPDFIUM_INFO.tag == "5.13.0"


def test_phase3_policy_values_are_fixed() -> None:
    assert MAX_PDF_BYTES == 50 * 1024 * 1024
    assert MAX_TRANSIENT_RETRIES == 2
    assert MAX_AUTHORIZED_REDIRECTS == 5
    assert FETCH_TIMEOUT_MS == 120_000
    assert FILE_TYPE is FileType.PDF_ONLY


def test_closed_enums_expose_only_the_approved_values() -> None:
    assert {member.value for member in IngestionStatus} == {
        "NEVER_ATTEMPTED",
        "READY",
        "FAILED",
    }
    assert {member.value for member in ExtractionStatus} == {
        "SUCCESS",
        "PARTIAL",
        "NO_TEXT",
    }
    assert {member.value for member in TextSource} == {"EMBEDDED_PDF"}
    assert {member.value for member in IngestionErrorCode} == {
        "NETWORK_TIMEOUT",
        "NETWORK_FAILURE",
        "DOWNLOAD_TOO_LARGE",
        "UNAUTHORIZED_REDIRECT",
        "UNAUTHORIZED_ROUTE",
        "INVALID_PDF",
        "PDF_ENCRYPTED",
        "PDF_PARSE_FAILED",
        "STORAGE_FAILED",
        "PERSISTENCE_FAILED",
    }
    assert {member.value for member in IngestionMode} == {"INGEST_NEW", "REFRESH"}
    assert {member.value for member in FileType} == {"PDF_ONLY"}
    assert {member.value for member in DocumentResultCode} == {
        "INGESTED",
        "UNCHANGED",
        "FAILED",
        "SKIPPED",
    }


def test_batch_exit_code_is_nonzero_only_when_any_document_failed() -> None:
    success = BatchIngestionResult(1, 1, 0, 0, 0, 1, 0, ())
    failure = BatchIngestionResult(1, 0, 0, 1, 0, 0, 0, ())

    assert success.exit_code == 0
    assert failure.exit_code == 1


def test_fetched_pdf_body_is_excluded_from_repr() -> None:
    fetched = FetchedPdf(
        body=b"private-pdf-bytes",
        source_filename="lecture.pdf",
        advisory_media_type="application/pdf",
    )

    assert "private-pdf-bytes" not in repr(fetched)
    assert fetched.body == b"private-pdf-bytes"


def test_document_ingestion_failure_retains_only_safe_code_and_message() -> None:
    failure = DocumentIngestionFailure(IngestionErrorCode.INVALID_PDF)

    assert failure.code is IngestionErrorCode.INVALID_PDF
    assert str(failure) == "document ingestion failed"
    assert repr(failure.args) == "('document ingestion failed',)"


def test_contract_records_are_frozen_and_slot_backed() -> None:
    selection = RemoteFileSelection(1, 2, "moodle:site", "resource-cm:3", "Lecture")
    page = ExtractedPage(1, "Text", TextSource.EMBEDDED_PDF, 612.0, 792.0)
    parsed = ParsedPdf(1, ExtractionStatus.SUCCESS, (page,))
    blob = BlobInstall("a" * 64, 12, "sha256/aa/hash.pdf", True)
    document = DocumentIngestionResult(
        remote_file_id=1,
        code=DocumentResultCode.INGESTED,
        version_number=1,
        size_bytes=12,
        page_count=1,
        extraction_status=ExtractionStatus.SUCCESS,
    )
    batch = BatchIngestionResult(1, 1, 0, 0, 0, 1, 0, (document,))

    for record in (selection, page, parsed, blob, document, batch):
        assert not hasattr(record, "__dict__")

    with pytest.raises(FrozenInstanceError):
        selection.title = "Changed"  # type: ignore[misc]
