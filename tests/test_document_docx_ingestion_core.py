from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

import aaos.documents as documents
import aaos.documents.contracts as contracts
from aaos.documents.contracts import (
    BlobInstall,
    DocumentIngestionFailure,
    DocumentResultCode,
    ExtractedStructureUnit,
    ExtractionStatus,
    FileType,
    IngestionErrorCode,
    IngestionMode,
    ParsedDocx,
    RemoteFileSelection,
    StructureUnitKind,
)
from aaos.documents.repository import PersistedVersion, SelectionPlan
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import (
    ContentBlobStore,
    StagedBlob,
    sanitize_source_filename,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _selection() -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=1,
        course_id=1,
        source_system="moodle:fixture",
        source_id="resource-cm:1",
        title="lecture.docx",
    )


def _parsed_docx() -> ParsedDocx:
    return ParsedDocx(
        units=(
            ExtractedStructureUnit(
                ordinal=1,
                kind=StructureUnitKind.HEADING,
                text="Overview",
                heading_level=1,
                table_index=None,
                row_index=None,
                column_index=None,
            ),
            ExtractedStructureUnit(
                ordinal=2,
                kind=StructureUnitKind.PARAGRAPH,
                text="Body",
                heading_level=None,
                table_index=None,
                row_index=None,
                column_index=None,
            ),
        )
    )


class _Repository:
    def __init__(self) -> None:
        self.structural_calls: list[tuple[str, str]] = []
        self.failures: list[IngestionErrorCode] = []

    def plan_selection(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None,
    ) -> SelectionPlan:
        del mode, source_system, remote_file_id
        item = _selection()
        return SelectionPlan((item,), (item,), ())

    def existing_version_for_sha256(
        self,
        *,
        remote_file_id: int,
        sha256: str,
    ) -> int | None:
        del remote_file_id, sha256
        raise AssertionError("DOCX must not use PDF unchanged lookup")

    def existing_structural_version_for_sha256(
        self,
        *,
        remote_file_id: int,
        sha256: str,
    ) -> int | None:
        del remote_file_id, sha256
        return None

    def persist_success(self, **_kwargs: object) -> PersistedVersion:
        raise AssertionError("DOCX must not use PDF persistence")

    def persist_structural_success(
        self,
        *,
        selection: RemoteFileSelection,
        blob: BlobInstall,
        source_filename: str,
        parsed_docx: ParsedDocx,
        attempted_at: datetime,
    ) -> PersistedVersion:
        assert selection == _selection()
        assert source_filename == "lecture.docx"
        assert parsed_docx == _parsed_docx()
        assert attempted_at == NOW
        assert blob.storage_key.endswith(".docx")
        self.structural_calls.append((source_filename, blob.storage_key))
        return PersistedVersion(
            document_id=1,
            version_number=1,
            content_blob_id=1,
            content_blob_created=True,
        )

    def record_failure(
        self,
        *,
        selection: RemoteFileSelection,
        code: IngestionErrorCode,
        attempted_at: datetime,
    ) -> None:
        del selection, attempted_at
        self.failures.append(code)

    def blob_is_referenced(self, *, sha256: str) -> bool:
        del sha256
        return False


class _Store:
    def __init__(self) -> None:
        self.install_suffixes: list[str] = []
        self.discarded = 0

    def stage(self, body: bytes) -> StagedBlob:
        digest = hashlib.sha256(body).hexdigest()
        return StagedBlob(
            sha256=digest,
            size_bytes=len(body),
            temporary_path=Path(f"C:/tmp/{digest}.tmp"),
        )

    def install(
        self,
        staged: StagedBlob,
        *,
        suffix: str = ".pdf",
    ) -> BlobInstall:
        self.install_suffixes.append(suffix)
        return BlobInstall(
            sha256=staged.sha256,
            size_bytes=staged.size_bytes,
            storage_key=f"sha256/{staged.sha256[:2]}/{staged.sha256}{suffix}",
            created=True,
        )

    def discard_temporary(self, staged: StagedBlob) -> None:
        del staged
        self.discarded += 1

    def compensate_new_blob(
        self,
        install: BlobInstall,
        *,
        referenced: bool,
    ) -> None:
        del install, referenced


class _PdfProcessor:
    def process(self, body: bytes) -> object:
        del body
        raise AssertionError("DOCX must not use PdfProcessor")


class _DocxProcessor:
    def __init__(
        self,
        failure: DocumentIngestionFailure | None = None,
    ) -> None:
        self.failure = failure
        self.calls = 0

    def process(self, body: bytes) -> ParsedDocx:
        assert body == b"docx-body"
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return _parsed_docx()


class _Fetcher:
    def __init__(self, fetched: object) -> None:
        self.fetched = fetched

    def supports(self, selection: RemoteFileSelection) -> bool:
        del selection
        return True

    def fetch(self, selection: RemoteFileSelection) -> object:
        assert selection == _selection()
        return self.fetched


class _FetcherSession(AbstractContextManager[_Fetcher]):
    def __init__(self, fetcher: _Fetcher) -> None:
        self.fetcher = fetcher

    def __enter__(self) -> _Fetcher:
        return self.fetcher

    def __exit__(self, *_args: object) -> None:
        return None


class _LockLease(AbstractContextManager[None]):
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


class _Lock:
    def acquire(self) -> _LockLease:
        return _LockLease()


def _fetched_docx() -> object:
    fetched_type = getattr(contracts, "FetchedDocx", None)
    assert fetched_type is not None, "FetchedDocx contract is missing"

    return fetched_type(
        body=b"docx-body",
        source_filename="unsafe/lecture.docx",
        advisory_media_type=DOCX_MIME,
    )


def test_fetched_docx_is_additive_and_keeps_pdf_only_phase3_flag() -> None:
    fetched_type = getattr(contracts, "FetchedDocx", None)

    assert fetched_type is not None
    assert getattr(documents, "FetchedDocx", None) is fetched_type

    fetched = fetched_type(
        body=b"private-docx-bytes",
        source_filename="lecture.docx",
        advisory_media_type=DOCX_MIME,
    )

    assert fetched.body == b"private-docx-bytes"
    assert "private-docx-bytes" not in repr(fetched)

    # C2 must not silently reinterpret the historical Phase-3 flag.
    assert {member.value for member in FileType} == {"PDF_ONLY"}


def test_docx_filename_fallback_and_storage_key_use_exact_docx_suffix(
    tmp_path: Path,
) -> None:
    assert (
        sanitize_source_filename(
            None,
            fallback="remote-file-7.docx",
            suffix=".docx",
        )
        == "remote-file-7.docx"
    )

    root = (tmp_path / "store").resolve()
    store = ContentBlobStore(root)
    staged = store.stage(b"docx-storage-body")

    install = store.install(staged, suffix=".docx")

    assert install.storage_key.endswith(".docx")
    assert not install.storage_key.endswith(".pdf")
    assert (root / Path(*install.storage_key.split("/"))).read_bytes() == (
        b"docx-storage-body"
    )


def test_storage_rejects_any_suffix_other_than_pdf_or_docx(
    tmp_path: Path,
) -> None:
    store = ContentBlobStore((tmp_path / "store").resolve())
    staged = store.stage(b"unsupported-suffix")

    with pytest.raises(DocumentIngestionFailure) as caught:
        store.install(staged, suffix=".exe")

    assert caught.value.code is IngestionErrorCode.STORAGE_FAILED
    assert staged.temporary_path.exists()


def test_service_dispatches_fetched_docx_to_structural_pipeline() -> None:
    repository = _Repository()
    store = _Store()
    docx_processor = _DocxProcessor()
    fetched = _fetched_docx()
    fetcher = _Fetcher(fetched)

    service = Phase3IngestionService(
        repository=cast(Any, repository),
        blob_store=cast(Any, store),
        pdf_processor=cast(Any, _PdfProcessor()),
        docx_processor=cast(Any, docx_processor),
        ingestion_lock=cast(Any, _Lock()),
        fetcher_factory=cast(
            Any,
            lambda: _FetcherSession(fetcher),
        ),
        clock=lambda: NOW,
    )

    result = service.run(
        mode=IngestionMode.INGEST_NEW,
        source_system="moodle:fixture",
    )

    assert result.ingested == 1
    assert result.failed == 0
    assert result.documents[0].code is DocumentResultCode.INGESTED
    assert result.documents[0].page_count is None
    assert result.documents[0].extraction_status is ExtractionStatus.SUCCESS

    assert docx_processor.calls == 1
    assert store.install_suffixes == [".docx"]
    assert store.discarded == 1
    assert len(repository.structural_calls) == 1
    assert repository.failures == []


def test_invalid_docx_records_docx_failure_without_installing_blob() -> None:
    repository = _Repository()
    store = _Store()
    failure = DocumentIngestionFailure(IngestionErrorCode.INVALID_DOCX)
    docx_processor = _DocxProcessor(failure)
    fetcher = _Fetcher(_fetched_docx())

    service = Phase3IngestionService(
        repository=cast(Any, repository),
        blob_store=cast(Any, store),
        pdf_processor=cast(Any, _PdfProcessor()),
        docx_processor=cast(Any, docx_processor),
        ingestion_lock=cast(Any, _Lock()),
        fetcher_factory=cast(
            Any,
            lambda: _FetcherSession(fetcher),
        ),
        clock=lambda: NOW,
    )

    result = service.run(
        mode=IngestionMode.INGEST_NEW,
        source_system="moodle:fixture",
    )

    assert result.failed == 1
    assert result.documents[0].error_code is IngestionErrorCode.INVALID_DOCX
    assert store.install_suffixes == []
    assert store.discarded == 1
    assert repository.structural_calls == []
    assert repository.failures == [IngestionErrorCode.INVALID_DOCX]
