"""Deterministic orchestration for Phase 3 document ingestion."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Protocol

from aaos.documents.contracts import (
    MAX_TRANSIENT_RETRIES,
    BatchIngestionResult,
    BlobInstall,
    DocumentIngestionFailure,
    DocumentIngestionResult,
    DocumentResultCode,
    ExtractionStatus,
    FetchedDocx,
    FetchedPdf,
    IngestionErrorCode,
    IngestionMode,
    RemoteFileSelection,
)
from aaos.documents.docx import DocxProcessor
from aaos.documents.lock import IngestionLock
from aaos.documents.pdf import PdfProcessor
from aaos.documents.repository import DocumentRepository, SelectionPlan
from aaos.documents.storage import ContentBlobStore, StagedBlob
from aaos.documents.storage import sanitize_source_filename as sanitize_source_filename


class PdfFetcher(Protocol):
    """Legacy-named fetch boundary for supported Phase-3 document payloads."""

    def fetch(
        self,
        selection: RemoteFileSelection,
    ) -> FetchedPdf | FetchedDocx: ...


_RETRYABLE_FETCH_ERRORS = frozenset(
    {
        IngestionErrorCode.NETWORK_TIMEOUT,
        IngestionErrorCode.NETWORK_FAILURE,
    }
)


class Phase3IngestionService:
    """Run one lock-scoped, sequential ingestion batch."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        blob_store: ContentBlobStore,
        pdf_processor: PdfProcessor,
        ingestion_lock: IngestionLock,
        fetcher_factory: Callable[[], AbstractContextManager[PdfFetcher]],
        clock: Callable[[], datetime],
        docx_processor: DocxProcessor | None = None,
    ) -> None:
        self._repository = repository
        self._blob_store = blob_store
        self._pdf_processor = pdf_processor
        self._docx_processor = docx_processor or DocxProcessor()
        self._ingestion_lock = ingestion_lock
        self._fetcher_factory = fetcher_factory
        self._clock = clock

    def run(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None = None,
    ) -> BatchIngestionResult:
        """Run a bounded batch while holding the process-wide ingestion lock."""
        with self._ingestion_lock.acquire():
            plan = self._repository.plan_selection(
                mode=mode,
                source_system=source_system,
                remote_file_id=remote_file_id,
            )
            results = {
                selection.remote_file_id: DocumentIngestionResult(
                    remote_file_id=selection.remote_file_id,
                    code=DocumentResultCode.SKIPPED,
                )
                for selection in plan.skipped
            }
            if plan.fetch:
                with self._fetcher_factory() as fetcher:
                    for selection in plan.fetch:
                        results[selection.remote_file_id] = self._ingest_one(
                            selection=selection,
                            fetcher=fetcher,
                        )
            ordered = tuple(results[item.remote_file_id] for item in plan.selected)
            return _batch_result(plan=plan, documents=ordered)

    def _ingest_one(
        self,
        *,
        selection: RemoteFileSelection,
        fetcher: PdfFetcher,
    ) -> DocumentIngestionResult:
        attempted_at = self._attempted_at()
        if attempted_at is None:
            return _failed_result(selection, IngestionErrorCode.PERSISTENCE_FAILED)

        staged: StagedBlob | None = None
        installed: BlobInstall | None = None
        result: DocumentIngestionResult | None = None
        failure_code: IngestionErrorCode | None = None
        try:
            if not _supports_selection(fetcher, selection):
                return DocumentIngestionResult(
                    remote_file_id=selection.remote_file_id,
                    code=DocumentResultCode.SKIPPED,
                )

            fetched = self._fetch_with_retries(
                fetcher=fetcher,
                selection=selection,
            )
            if isinstance(fetched, FetchedDocx):
                source_filename = sanitize_source_filename(
                    fetched.source_filename,
                    fallback=f"remote-file-{selection.remote_file_id}.docx",
                    suffix=".docx",
                )
                staged = self._blob_store.stage(fetched.body)
                parsed_docx = self._docx_processor.process(fetched.body)
                existing_version = (
                    self._repository.existing_structural_version_for_sha256(
                        remote_file_id=selection.remote_file_id,
                        sha256=staged.sha256,
                    )
                )
                if existing_version is not None:
                    result = DocumentIngestionResult(
                        remote_file_id=selection.remote_file_id,
                        code=DocumentResultCode.UNCHANGED,
                        version_number=existing_version,
                        size_bytes=staged.size_bytes,
                        extraction_status=ExtractionStatus.SUCCESS,
                    )
                else:
                    installed = self._blob_store.install(
                        staged,
                        suffix=".docx",
                    )
                    persisted = self._repository.persist_structural_success(
                        selection=selection,
                        blob=installed,
                        source_filename=source_filename,
                        parsed_docx=parsed_docx,
                        attempted_at=attempted_at,
                    )
                    result = DocumentIngestionResult(
                        remote_file_id=selection.remote_file_id,
                        code=DocumentResultCode.INGESTED,
                        version_number=persisted.version_number,
                        size_bytes=installed.size_bytes,
                        extraction_status=ExtractionStatus.SUCCESS,
                        deduplicated=not persisted.content_blob_created,
                    )
            elif isinstance(fetched, FetchedPdf):
                source_filename = sanitize_source_filename(
                    fetched.source_filename,
                    fallback=f"remote-file-{selection.remote_file_id}.pdf",
                )
                staged = self._blob_store.stage(fetched.body)
                parsed_pdf = self._pdf_processor.process(fetched.body)
                existing_version = self._repository.existing_version_for_sha256(
                    remote_file_id=selection.remote_file_id,
                    sha256=staged.sha256,
                )
                if existing_version is not None:
                    result = DocumentIngestionResult(
                        remote_file_id=selection.remote_file_id,
                        code=DocumentResultCode.UNCHANGED,
                        version_number=existing_version,
                        size_bytes=staged.size_bytes,
                        page_count=parsed_pdf.page_count,
                        extraction_status=parsed_pdf.extraction_status,
                    )
                else:
                    installed = self._blob_store.install(staged)
                    persisted = self._repository.persist_success(
                        selection=selection,
                        blob=installed,
                        source_filename=source_filename,
                        parsed_pdf=parsed_pdf,
                        attempted_at=attempted_at,
                    )
                    result = DocumentIngestionResult(
                        remote_file_id=selection.remote_file_id,
                        code=DocumentResultCode.INGESTED,
                        version_number=persisted.version_number,
                        size_bytes=installed.size_bytes,
                        page_count=parsed_pdf.page_count,
                        extraction_status=parsed_pdf.extraction_status,
                        deduplicated=not persisted.content_blob_created,
                    )
            else:
                raise DocumentIngestionFailure(
                    IngestionErrorCode.UNAUTHORIZED_ROUTE
                )
        except DocumentIngestionFailure as failure:
            failure_code = failure.code
            if failure.code is IngestionErrorCode.PERSISTENCE_FAILED:
                self._compensate_after_persistence_failure(installed)
        finally:
            if staged is not None:
                try:
                    self._blob_store.discard_temporary(staged)
                except DocumentIngestionFailure:
                    if failure_code is None:
                        failure_code = IngestionErrorCode.STORAGE_FAILED
                    result = None

        if failure_code is not None:
            return self._record_failure(
                selection=selection,
                code=failure_code,
                attempted_at=attempted_at,
            )
        if result is None:
            return self._record_failure(
                selection=selection,
                code=IngestionErrorCode.PERSISTENCE_FAILED,
                attempted_at=attempted_at,
            )
        return result

    def _fetch_with_retries(
        self,
        *,
        fetcher: PdfFetcher,
        selection: RemoteFileSelection,
    ) -> FetchedPdf | FetchedDocx:
        for attempt in range(MAX_TRANSIENT_RETRIES + 1):
            try:
                return fetcher.fetch(selection)
            except DocumentIngestionFailure as failure:
                if failure.code not in _RETRYABLE_FETCH_ERRORS or attempt >= MAX_TRANSIENT_RETRIES:
                    raise
        raise AssertionError("bounded fetch loop did not return or raise")

    def _compensate_after_persistence_failure(self, installed: BlobInstall | None) -> None:
        if installed is None or not installed.created:
            return
        try:
            referenced = self._repository.blob_is_referenced(sha256=installed.sha256)
        except DocumentIngestionFailure:
            return
        if referenced:
            return
        try:
            self._blob_store.compensate_new_blob(installed, referenced=False)
        except DocumentIngestionFailure:
            return

    def _record_failure(
        self,
        *,
        selection: RemoteFileSelection,
        code: IngestionErrorCode,
        attempted_at: datetime,
    ) -> DocumentIngestionResult:
        try:
            self._repository.record_failure(
                selection=selection,
                code=code,
                attempted_at=attempted_at,
            )
        except DocumentIngestionFailure:
            code = IngestionErrorCode.PERSISTENCE_FAILED
        return _failed_result(selection, code)

    def _attempted_at(self) -> datetime | None:
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None:
                return None
            if value.utcoffset() is None:
                return None
            return value.astimezone(timezone.utc)
        except (OverflowError, TypeError, ValueError):
            return None


def _supports_selection(
    fetcher: PdfFetcher,
    selection: RemoteFileSelection,
) -> bool:
    """Honor optional provider capability while preserving legacy fetchers."""
    supports = getattr(fetcher, "supports", None)
    if supports is None:
        return True
    if not callable(supports):
        return False
    return supports(selection) is True


def _failed_result(
    selection: RemoteFileSelection,
    code: IngestionErrorCode,
) -> DocumentIngestionResult:
    return DocumentIngestionResult(
        remote_file_id=selection.remote_file_id,
        code=DocumentResultCode.FAILED,
        error_code=code,
    )


def _batch_result(
    *,
    plan: SelectionPlan,
    documents: tuple[DocumentIngestionResult, ...],
) -> BatchIngestionResult:
    ingested = sum(item.code is DocumentResultCode.INGESTED for item in documents)
    unchanged = sum(item.code is DocumentResultCode.UNCHANGED for item in documents)
    failed = sum(item.code is DocumentResultCode.FAILED for item in documents)
    skipped = sum(item.code is DocumentResultCode.SKIPPED for item in documents)
    return BatchIngestionResult(
        remote_files_selected=len(plan.selected),
        ingested=ingested,
        unchanged=unchanged,
        failed=failed,
        skipped=skipped,
        new_versions=ingested,
        deduplicated_blobs=sum(
            item.code is DocumentResultCode.INGESTED and item.deduplicated for item in documents
        ),
        documents=documents,
    )
